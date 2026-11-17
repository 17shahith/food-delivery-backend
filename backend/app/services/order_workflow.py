"""Order status state machine, access rules and side-effects (timeline, realtime, notifications)."""

import uuid

from app.models.enums import OrderStatus, UserRole
from app.models.order import Order, StatusTimeline
from app.models.user import User
from app.services.base import BaseService
from app.services.notification_service import CUSTOMER_STATUS_MESSAGES, NotificationService
from app.services.realtime_service import ADMIN_TOPIC, order_topic
from app.utils.exceptions import InvalidStatusTransitionError
from app.utils.helpers import utcnow

S = OrderStatus

ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    S.ORDER_PLACED: frozenset({S.VENDOR_ACCEPTED, S.REJECTED, S.CANCELLED}),
    S.VENDOR_ACCEPTED: frozenset({S.PREPARING, S.CANCELLED}),
    S.PREPARING: frozenset({S.READY_FOR_PICKUP, S.CANCELLED}),
    S.READY_FOR_PICKUP: frozenset({S.RIDER_ASSIGNED, S.CANCELLED}),
    S.RIDER_ASSIGNED: frozenset({S.PICKED_UP, S.CANCELLED}),
    S.PICKED_UP: frozenset({S.OUT_FOR_DELIVERY}),
    S.OUT_FOR_DELIVERY: frozenset({S.DELIVERED}),
    S.DELIVERED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
}

# Who may move an order INTO a status through PUT /orders/{id}/status.
# RIDER_ASSIGNED is only reachable through the rider assignment flow; CANCELLED via /cancel rules.
STATUS_ACTORS: dict[OrderStatus, frozenset[UserRole]] = {
    S.VENDOR_ACCEPTED: frozenset({UserRole.VENDOR, UserRole.ADMIN}),
    S.REJECTED: frozenset({UserRole.VENDOR, UserRole.ADMIN}),
    S.PREPARING: frozenset({UserRole.VENDOR, UserRole.ADMIN}),
    S.READY_FOR_PICKUP: frozenset({UserRole.VENDOR, UserRole.ADMIN}),
    S.PICKED_UP: frozenset({UserRole.RIDER, UserRole.ADMIN}),
    S.OUT_FOR_DELIVERY: frozenset({UserRole.RIDER, UserRole.ADMIN}),
    S.DELIVERED: frozenset({UserRole.RIDER, UserRole.ADMIN}),
}

CANCELLABLE_BY: dict[UserRole, frozenset[OrderStatus]] = {
    UserRole.CUSTOMER: frozenset({S.ORDER_PLACED}),
    UserRole.VENDOR: frozenset({S.ORDER_PLACED, S.VENDOR_ACCEPTED, S.PREPARING, S.READY_FOR_PICKUP, S.RIDER_ASSIGNED}),
    UserRole.ADMIN: frozenset({S.ORDER_PLACED, S.VENDOR_ACCEPTED, S.PREPARING, S.READY_FOR_PICKUP, S.RIDER_ASSIGNED}),
}

DEFAULT_DESCRIPTIONS: dict[OrderStatus, str] = {
    S.ORDER_PLACED: "Order placed by customer",
    S.VENDOR_ACCEPTED: "Vendor accepted the order",
    S.PREPARING: "Vendor is preparing the order",
    S.READY_FOR_PICKUP: "Order is ready for pickup",
    S.RIDER_ASSIGNED: "Rider accepted the delivery",
    S.PICKED_UP: "Rider picked up the order",
    S.OUT_FOR_DELIVERY: "Order is out for delivery",
    S.DELIVERED: "Order delivered",
    S.REJECTED: "Vendor rejected the order",
    S.CANCELLED: "Order cancelled",
}

VENDOR_STATUS_MESSAGES: dict[OrderStatus, tuple[str, str]] = {
    S.RIDER_ASSIGNED: ("Rider assigned", "{rider} accepted delivery of order {number}."),
    S.PICKED_UP: ("Order picked up", "{rider} picked up order {number}."),
    S.DELIVERED: ("Order delivered", "Order {number} was delivered."),
    S.CANCELLED: ("Order cancelled", "Order {number} was cancelled."),
}


def validate_transition(current: OrderStatus, new: OrderStatus) -> None:
    allowed = ALLOWED_TRANSITIONS[current]
    if new not in allowed:
        raise InvalidStatusTransitionError(
            f"Cannot change order status from {current.value} to {new.value}",
            details={"current": current.value, "requested": new.value, "allowed": sorted(s.value for s in allowed)},
        )


def is_vendor_owner(order: Order, user: User) -> bool:
    return order.vendor.user_id == user.id


def is_assigned_rider(order: Order, user: User) -> bool:
    return order.rider is not None and order.rider.user_id == user.id


def can_view_order(order: Order, user: User) -> bool:
    return (
        user.role == UserRole.ADMIN
        or order.customer_id == user.id
        or is_vendor_owner(order, user)
        or is_assigned_rider(order, user)
    )


class OrderWorkflow(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.notifications = NotificationService(session, publisher)

    def apply_status(
        self, order: Order, new_status: OrderStatus, actor_id: uuid.UUID | None, description: str | None = None
    ) -> StatusTimeline:
        """Mutates the order and records side effects. Caller validates and commits.

        Requires `order.vendor` and `order.rider` (with `.user`) to be loaded.
        """
        now = utcnow()
        order.order_status = new_status
        order.updated_at = now
        entry = StatusTimeline(
            order_id=order.id,
            status=new_status,
            description=description or DEFAULT_DESCRIPTIONS[new_status],
            updated_by=actor_id,
            created_at=now,
        )
        self.session.add(entry)

        payload = {
            "order_id": order.id,
            "order_number": order.order_number,
            "status": new_status.value,
            "description": entry.description,
            "updated_by": actor_id,
            "rider_id": order.rider_id,
            "updated_at": now,
        }
        self.queue_event(order_topic(order.id), "status_changed", payload)
        self.queue_event(ADMIN_TOPIC, "order_status_changed", payload)
        self._notify(order, new_status, actor_id)
        return entry

    def _notify(self, order: Order, status: OrderStatus, actor_id: uuid.UUID | None) -> None:
        context = {
            "number": order.order_number,
            "shop": order.vendor.shop_name,
            "rider": order.rider.user.name if order.rider is not None else "Your rider",
        }
        type_ = f"order_{status.value.lower()}"
        if status in CUSTOMER_STATUS_MESSAGES and order.customer_id != actor_id:
            title, template = CUSTOMER_STATUS_MESSAGES[status]
            self.notifications.notify(order.customer_id, title, template.format(**context), type_=type_, order_id=order.id)
        if status in VENDOR_STATUS_MESSAGES and order.vendor.user_id != actor_id:
            title, template = VENDOR_STATUS_MESSAGES[status]
            self.notifications.notify(order.vendor.user_id, title, template.format(**context), type_=type_, order_id=order.id)
        if status == S.CANCELLED and order.rider is not None and order.rider.user_id != actor_id:
            self.notifications.notify(
                order.rider.user_id,
                "Delivery cancelled",
                f"Order {order.order_number} was cancelled; you are released from this delivery.",
                type_=type_,
                order_id=order.id,
            )
