import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.enums import (
    TERMINAL_ORDER_STATUSES,
    ApprovalStatus,
    AssignmentStatus,
    OrderStatus,
    PaymentMethod,
    RiderAvailability,
    UserRole,
)
from app.models.order import Checkout, Order, OrderItem, StatusTimeline
from app.models.product import Product
from app.models.user import User
from app.repositories.order_repository import ORDER_LIST_OPTIONS, OrderRepository
from app.repositories.product_repository import ProductRepository
from app.repositories.rider_repository import AssignmentRepository, RiderRepository
from app.repositories.vendor_repository import VendorRepository
from app.schemas.order import CheckoutCreate, CheckoutResponse, OrderOut
from app.services.base import BaseService
from app.services.order_workflow import (
    CANCELLABLE_BY,
    STATUS_ACTORS,
    OrderWorkflow,
    can_view_order,
    is_assigned_rider,
    is_vendor_owner,
    validate_transition,
)
from app.services.realtime_service import ADMIN_TOPIC
from app.utils.exceptions import (
    BadRequestError,
    ConflictError,
    InvalidStatusTransitionError,
    NotFoundError,
    PermissionDeniedError,
)
from app.utils.helpers import generate_checkout_reference, generate_order_number, haversine_km, money, utcnow

MAX_QUANTITY_PER_PRODUCT = 100


class OrderService(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.repo = OrderRepository(session)
        self.products = ProductRepository(session)
        self.vendors = VendorRepository(session)
        self.riders = RiderRepository(session)
        self.assignments = AssignmentRepository(session)
        self.workflow = OrderWorkflow(session, publisher)

    # ------------------------------------------------------------------ checkout
    async def checkout(
        self,
        customer: User,
        data: CheckoutCreate,
        idempotency_key: str | None,
        *,
        payment_method: PaymentMethod = PaymentMethod.COD,
        delivery_details: dict | None = None,
        only_vendor_id: uuid.UUID | None = None,
    ) -> CheckoutResponse:
        """Create one order per vendor in a single transaction.

        `only_vendor_id` enforces a single-shop cart (used by the web app's checkout).
        """
        if idempotency_key:
            existing = await self.repo.get_checkout_by_key(customer.id, idempotency_key)
            if existing:
                return await self._checkout_response(existing)

        quantities: dict[uuid.UUID, int] = defaultdict(int)
        for item in data.items:
            quantities[item.product_id] += item.quantity
        products = {p.id: p for p in await self.products.get_many_with_vendor(list(quantities))}

        missing = [str(pid) for pid in quantities if pid not in products]
        if missing:
            raise NotFoundError("Some products were not found", code="PRODUCT_NOT_FOUND", details={"product_ids": missing})
        if only_vendor_id is not None and any(p.vendor_id != only_vendor_id for p in products.values()):
            raise BadRequestError("An item in your cart is not sold by this shop", code="MIXED_VENDOR_CART")
        self._validate_orderable(products.values(), quantities)

        now = utcnow()
        checkout = Checkout(
            reference=generate_checkout_reference(now),
            customer_id=customer.id,
            idempotency_key=idempotency_key,
            total_amount=Decimal("0"),
        )
        by_vendor: dict[uuid.UUID, list[Product]] = defaultdict(list)
        for product in products.values():
            by_vendor[product.vendor_id].append(product)

        grand_total = Decimal("0")
        for vendor_products in by_vendor.values():
            vendor = vendor_products[0].vendor
            items = [
                OrderItem(product_id=p.id, product_name=p.name, quantity=quantities[p.id], unit_price=p.price)
                for p in vendor_products
            ]
            for p in vendor_products:
                if p.stock is not None:
                    p.stock -= quantities[p.id]
            subtotal = money(sum((p.price * quantities[p.id] for p in vendor_products), Decimal("0")))
            if vendor.min_order_amount is not None and subtotal < vendor.min_order_amount:
                raise ConflictError(
                    f"Minimum order at {vendor.shop_name} is {money(vendor.min_order_amount)}",
                    code="MIN_ORDER_NOT_MET",
                    details={"vendor_id": str(vendor.id), "min_order_amount": float(vendor.min_order_amount)},
                )
            delivery_fee = money(vendor.delivery_fee or 0)
            total = money(subtotal + delivery_fee)
            grand_total += total
            order = Order(
                id=uuid.uuid4(),
                order_number=generate_order_number(now),
                customer_id=customer.id,
                vendor=vendor,
                checkout=checkout,
                subtotal=subtotal,
                delivery_fee=delivery_fee,
                total_amount=total,
                payment_method=payment_method,
                rider_earning=self._rider_earning(vendor, data.delivery_latitude, data.delivery_longitude),
                order_status=OrderStatus.ORDER_PLACED,
                delivery_address=data.delivery_address,
                delivery_latitude=data.delivery_latitude,
                delivery_longitude=data.delivery_longitude,
                delivery_details=delivery_details,
                notes=data.notes,
                items=items,
                timeline=[
                    StatusTimeline(
                        status=OrderStatus.ORDER_PLACED,
                        description="Order placed by customer",
                        updated_by=customer.id,
                        created_at=now,
                    )
                ],
            )
            self.session.add(order)
            # Flush so the order row exists before notifications that reference it (no ORM relationship).
            await self.session.flush()
            self.workflow.notifications.notify(
                vendor.user_id,
                "New order received",
                f"New order {order.order_number} for {total} ({len(items)} item(s)).",
                type_="order_new",
                order_id=order.id,
            )
            self.workflow.notifications.notify(
                customer.id,
                "Order placed",
                f"Your order {order.order_number} from {vendor.shop_name} has been placed.",
                type_="order_order_placed",
                order_id=order.id,
            )
            self.queue_event(
                ADMIN_TOPIC,
                "order_created",
                {"order_id": order.id, "order_number": order.order_number, "vendor_id": vendor.id, "total_amount": total},
            )
        checkout.total_amount = money(grand_total)
        self.session.add(checkout)

        try:
            await self.commit()
        except IntegrityError:
            await self.rollback()
            # A concurrent request with the same idempotency key won the race: return its result.
            if idempotency_key:
                existing = await self.repo.get_checkout_by_key(customer.id, idempotency_key)
                if existing:
                    return await self._checkout_response(existing)
            raise ConflictError("Could not place the order, please retry", code="CHECKOUT_CONFLICT")
        return await self._checkout_response(checkout)

    @staticmethod
    def _rider_earning(vendor, latitude: float | None, longitude: float | None) -> Decimal:
        settings = get_settings()
        distance = haversine_km(vendor.latitude, vendor.longitude, latitude, longitude) if latitude is not None else 0
        return money(settings.RIDER_BASE_PAY + settings.RIDER_PAY_PER_KM * distance)

    @staticmethod
    def _validate_orderable(products, quantities: dict[uuid.UUID, int]) -> None:
        problems = []
        for product in products:
            vendor = product.vendor
            if not product.availability:
                reason = "is out of stock"
            elif vendor.status != ApprovalStatus.APPROVED:
                reason = "is from a shop that is not active"
            elif not vendor.is_open:
                reason = f"is unavailable because {vendor.shop_name} is closed right now"
            elif quantities[product.id] > MAX_QUANTITY_PER_PRODUCT:
                reason = f"exceeds the maximum quantity of {MAX_QUANTITY_PER_PRODUCT}"
            elif product.stock is not None and quantities[product.id] > product.stock:
                reason = f"has only {product.stock} left in stock"
            else:
                continue
            problems.append({"product_id": str(product.id), "name": product.name, "reason": reason})
        if problems:
            message = "; ".join(f"{p['name']} {p['reason']}" for p in problems)
            raise ConflictError(message, code="PRODUCT_UNAVAILABLE", details=problems)

    async def _checkout_response(self, checkout: Checkout) -> CheckoutResponse:
        orders = await self.repo.list_by_checkout(checkout.reference)
        return CheckoutResponse(
            checkout_reference=checkout.reference,
            total_amount=checkout.total_amount,
            orders=[OrderOut.model_validate(o) for o in orders],
        )

    # ------------------------------------------------------------------ queries
    async def list_orders(
        self,
        user: User,
        *,
        status: OrderStatus | None,
        checkout_reference: str | None,
        vendor_id: uuid.UUID | None,
        customer_id: uuid.UUID | None,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Order], int]:
        stmt = select(Order).options(*ORDER_LIST_OPTIONS)
        if user.role == UserRole.CUSTOMER:
            stmt = stmt.where(Order.customer_id == user.id)
        elif user.role == UserRole.VENDOR:
            vendor = await self.vendors.get_by_user_id(user.id)
            if vendor is None:
                return [], 0
            stmt = stmt.where(Order.vendor_id == vendor.id)
        elif user.role == UserRole.RIDER:
            rider = await self.riders.get_by_user_id(user.id)
            if rider is None:
                return [], 0
            stmt = stmt.where(Order.rider_id == rider.id)
        else:  # admin-only filters
            if vendor_id:
                stmt = stmt.where(Order.vendor_id == vendor_id)
            if customer_id:
                stmt = stmt.where(Order.customer_id == customer_id)
        if status:
            stmt = stmt.where(Order.order_status == status)
        if checkout_reference:
            stmt = stmt.where(Order.checkout_reference == checkout_reference)
        if date_from:
            stmt = stmt.where(Order.created_at >= date_from)
        if date_to:
            stmt = stmt.where(Order.created_at <= date_to)
        return await self.repo.paginate(stmt.order_by(Order.created_at.desc()), limit, offset)

    async def get_detail(self, order_id: uuid.UUID, user: User) -> Order:
        order = await self.repo.get_detail(order_id)
        if order is None or not can_view_order(order, user):
            raise NotFoundError("Order not found")
        return order

    # ------------------------------------------------------------------ status changes
    async def _load_for_update(self, order_id: uuid.UUID, user: User) -> Order:
        order = await self.repo.get_for_workflow(order_id)
        if order is None or not can_view_order(order, user):
            raise NotFoundError("Order not found")
        return order

    async def update_status(
        self, order_id: uuid.UUID, user: User, new_status: OrderStatus, description: str | None
    ) -> Order:
        if new_status == OrderStatus.CANCELLED:
            return await self.cancel(order_id, user, description)
        if new_status == OrderStatus.RIDER_ASSIGNED:
            raise BadRequestError(
                "Riders are assigned via POST /api/riders/assign and confirmed by the rider",
                code="USE_ASSIGNMENT_FLOW",
            )

        order = await self._load_for_update(order_id, user)
        validate_transition(order.order_status, new_status)
        allowed_roles = STATUS_ACTORS.get(new_status, frozenset())
        if user.role not in allowed_roles:
            raise PermissionDeniedError(f"Your role cannot set status {new_status.value}")
        if user.role == UserRole.VENDOR and not is_vendor_owner(order, user):
            raise PermissionDeniedError("This order belongs to another shop")
        if user.role == UserRole.RIDER and not is_assigned_rider(order, user):
            raise PermissionDeniedError("You are not the rider assigned to this order")

        if new_status == OrderStatus.REJECTED:
            order.cancellation_reason = description or "Rejected by the shop"
            await self._restock(order)
        self.workflow.apply_status(order, new_status, user.id, description)
        if new_status == OrderStatus.DELIVERED:
            await self._release_rider(order)
        await self.commit()
        return await self.get_detail(order.id, user)

    async def cancel(self, order_id: uuid.UUID, user: User, reason: str | None) -> Order:
        order = await self._load_for_update(order_id, user)
        if user.role == UserRole.VENDOR and not is_vendor_owner(order, user):
            raise PermissionDeniedError("This order belongs to another shop")
        if user.role == UserRole.CUSTOMER and order.customer_id != user.id:
            raise PermissionDeniedError("You can only cancel your own orders")
        allowed = CANCELLABLE_BY.get(user.role, frozenset())
        if order.order_status not in allowed:
            if order.order_status in TERMINAL_ORDER_STATUSES:
                raise InvalidStatusTransitionError(f"Order is already {order.order_status.value}")
            raise InvalidStatusTransitionError(
                f"Your role cannot cancel an order in status {order.order_status.value}",
                details={"current": order.order_status.value},
            )

        order.cancellation_reason = reason
        description = f"Cancelled by {user.role.value}" + (f": {reason}" if reason else "")
        active = await self.assignments.get_active_for_order(order.id)
        if active is not None:
            active.status = AssignmentStatus.CANCELLED
            active.responded_at = active.responded_at or utcnow()
        self.workflow.apply_status(order, OrderStatus.CANCELLED, user.id, description)
        await self._restock(order)
        await self._release_rider(order)
        await self.commit()
        return await self.get_detail(order.id, user)

    async def _restock(self, order: Order) -> None:
        """Return tracked stock for an order that will not be fulfilled."""
        rows = await self.session.execute(
            select(OrderItem.quantity, Product).join(Product, Product.id == OrderItem.product_id).where(
                OrderItem.order_id == order.id, Product.stock.is_not(None)
            )
        )
        for quantity, product in rows.all():
            product.stock += quantity

    async def _release_rider(self, order: Order) -> None:
        """Put the rider back online once they have no other active delivery."""
        if order.rider is None:
            return
        rider = order.rider
        if rider.availability == RiderAvailability.ON_DELIVERY:
            remaining = await self.repo.count_active_for_rider(rider.id, exclude_order_id=order.id)
            if remaining == 0:
                rider.availability = RiderAvailability.ONLINE
