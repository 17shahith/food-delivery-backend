from enum import StrEnum


class UserRole(StrEnum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    RIDER = "rider"
    ADMIN = "admin"


class ApprovalStatus(StrEnum):
    """Approval lifecycle shared by vendors and riders."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class RiderAvailability(StrEnum):
    OFFLINE = "offline"
    ONLINE = "online"
    ON_DELIVERY = "on_delivery"


class VehicleType(StrEnum):
    BICYCLE = "bicycle"
    MOTORCYCLE = "motorcycle"
    SCOOTER = "scooter"
    CAR = "car"
    ON_FOOT = "on_foot"


class OrderStatus(StrEnum):
    ORDER_PLACED = "ORDER_PLACED"
    VENDOR_ACCEPTED = "VENDOR_ACCEPTED"
    PREPARING = "PREPARING"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    RIDER_ASSIGNED = "RIDER_ASSIGNED"
    PICKED_UP = "PICKED_UP"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class PaymentMethod(StrEnum):
    COD = "cod"
    ONLINE = "online"


class AssignmentStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


ACTIVE_ORDER_STATUSES = frozenset(
    {
        OrderStatus.ORDER_PLACED,
        OrderStatus.VENDOR_ACCEPTED,
        OrderStatus.PREPARING,
        OrderStatus.READY_FOR_PICKUP,
        OrderStatus.RIDER_ASSIGNED,
        OrderStatus.PICKED_UP,
        OrderStatus.OUT_FOR_DELIVERY,
    }
)

TRACKABLE_ORDER_STATUSES = frozenset(
    {OrderStatus.RIDER_ASSIGNED, OrderStatus.PICKED_UP, OrderStatus.OUT_FOR_DELIVERY}
)

TERMINAL_ORDER_STATUSES = frozenset({OrderStatus.DELIVERED, OrderStatus.REJECTED, OrderStatus.CANCELLED})
