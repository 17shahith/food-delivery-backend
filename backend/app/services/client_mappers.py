"""Translate domain models <-> the web-app contract (see `app/schemas/client.py`)."""

from datetime import datetime

from app.models.address import CustomerAddress
from app.models.enums import ApprovalStatus, OrderStatus, RiderAvailability, VehicleType
from app.models.notification import Notification
from app.models.order import Order
from app.models.product import Product
from app.models.rider import Rider
from app.models.tracking import DeliveryCoordinate, RiderLocation
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.client import (
    AddressOut,
    AppNotification,
    AppOrder,
    AppOrderItem,
    AppProduct,
    AppRider,
    AppRiderLocation,
    AppUser,
    AppVendor,
    DeliveryAddressOut,
    OrderRiderBrief,
    OrderVendorBrief,
    ProductVendorBrief,
    StatusEventOut,
    UserBrief,
)
from app.utils.exceptions import BadRequestError
from app.utils.helpers import haversine_km

# ---------------------------------------------------------------------------- enums
_STATUS_TO_APP = {
    OrderStatus.ORDER_PLACED: "placed",
    OrderStatus.VENDOR_ACCEPTED: "accepted",
    OrderStatus.PREPARING: "preparing",
    OrderStatus.READY_FOR_PICKUP: "ready_for_pickup",
    OrderStatus.RIDER_ASSIGNED: "rider_assigned",
    OrderStatus.PICKED_UP: "picked_up",
    OrderStatus.OUT_FOR_DELIVERY: "out_for_delivery",
    OrderStatus.DELIVERED: "delivered",
    OrderStatus.REJECTED: "rejected",
    OrderStatus.CANCELLED: "cancelled",
}
_APP_TO_STATUS = {v: k for k, v in _STATUS_TO_APP.items()}


def app_status(status: OrderStatus) -> str:
    return _STATUS_TO_APP[status]


def domain_status(value: str) -> OrderStatus:
    try:
        return _APP_TO_STATUS[value]
    except KeyError as exc:
        raise BadRequestError(f"Unknown order status '{value}'") from exc


def parse_status_list(raw: str | None) -> list[OrderStatus] | None:
    """`status=placed,accepted` -> [ORDER_PLACED, VENDOR_ACCEPTED]."""
    if not raw:
        return None
    return [domain_status(part.strip()) for part in raw.split(",") if part.strip()]


def app_approval(status: ApprovalStatus | None) -> str | None:
    if status is None:
        return None
    # The app has no "suspended" state; a suspended account is shown as not approved.
    return "rejected" if status == ApprovalStatus.SUSPENDED else status.value


def app_vehicle(vehicle: VehicleType) -> str:
    return "motorbike" if vehicle == VehicleType.MOTORCYCLE else vehicle.value


def domain_vehicle(value: str) -> VehicleType:
    return VehicleType.MOTORCYCLE if value == "motorbike" else VehicleType(value)


def app_notification_type(raw: str) -> str:
    if raw in {"vendor_pending", "rider_pending", "vendor_status", "rider_status"}:
        return "approval"
    if raw.startswith("assignment") or raw in {"order_rider_assigned", "order_picked_up", "order_out_for_delivery"}:
        return "delivery"
    if raw.startswith("order"):
        return "order"
    if raw == "announcement":
        return "promo"
    return "system"


def _f(value) -> float | None:
    return None if value is None else float(value)


# ---------------------------------------------------------------------------- entities
def to_app_user(user: User, approval: ApprovalStatus | None = None) -> AppUser:
    return AppUser(
        id=user.id,
        email=user.email,
        full_name=user.name,
        phone=user.phone,
        role=user.role.value,
        avatar_url=user.avatar_url,
        is_active=user.is_active,
        approval_status=app_approval(approval),
        created_at=user.created_at,
    )


def to_user_brief(user: User | None, *, private: bool = False) -> UserBrief | None:
    if user is None:
        return None
    if private:
        return UserBrief(id=user.id, full_name=user.name, email=user.email, phone=user.phone, is_active=user.is_active)
    return UserBrief(id=user.id, full_name=user.name, phone=user.phone)


def to_address(address: CustomerAddress) -> AddressOut:
    return AddressOut(
        id=address.id, label=address.label, line1=address.line1, line2=address.line2, city=address.city,
        state=address.state, postal_code=address.postal_code, latitude=address.latitude,
        longitude=address.longitude, is_default=address.is_default,
    )


def to_app_vendor(vendor: Vendor, *, distance_km: float | None = None, owner: User | None = None) -> AppVendor:
    return AppVendor(
        id=vendor.id,
        user_id=vendor.user_id,
        name=vendor.shop_name,
        description=vendor.description,
        category=vendor.category,
        logo_url=vendor.logo_url,
        banner_url=vendor.banner_url,
        address=vendor.address,
        phone=vendor.phone,
        is_open=vendor.is_open,
        approval_status=app_approval(vendor.status),
        opening_hours=vendor.opening_hours,
        distance_km=distance_km,
        delivery_fee=float(vendor.delivery_fee or 0),
        min_order_amount=_f(vendor.min_order_amount),
        avg_prep_minutes=vendor.avg_prep_minutes,
        latitude=vendor.latitude,
        longitude=vendor.longitude,
        owner=to_user_brief(owner, private=True),
        created_at=vendor.created_at,
    )


def to_app_product(product: Product, vendor: Vendor | None = None) -> AppProduct:
    return AppProduct(
        id=product.id,
        vendor_id=product.vendor_id,
        name=product.name,
        description=product.description,
        price=float(product.price),
        category=product.category or "general",
        image_url=product.image_url,
        is_available=product.availability,
        stock=product.stock,
        unit=product.unit,
        vendor=(
            ProductVendorBrief(id=vendor.id, name=vendor.shop_name, is_open=vendor.is_open,
                               delivery_fee=float(vendor.delivery_fee or 0))
            if vendor is not None else None
        ),
        created_at=product.created_at,
    )


def to_app_order(order: Order) -> AppOrder:
    """Requires ORDER_DETAIL_OPTIONS (items+product, vendor, customer, timeline, rider+user)."""
    details = order.delivery_details or {}
    vendor = order.vendor
    distance = None
    if order.delivery_latitude is not None and order.delivery_longitude is not None:
        distance = round(haversine_km(vendor.latitude, vendor.longitude, order.delivery_latitude, order.delivery_longitude), 2)
    rider = order.rider
    return AppOrder(
        id=order.id,
        order_number=order.order_number,
        customer_id=order.customer_id,
        vendor_id=order.vendor_id,
        rider_id=order.rider_id,
        status=app_status(order.order_status),
        items=[
            AppOrderItem(
                id=item.id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=float(item.unit_price),
                subtotal=float(item.line_total),
                image_url=item.product.image_url if item.product is not None else None,
            )
            for item in order.items
        ],
        subtotal=float(order.subtotal),
        delivery_fee=float(order.delivery_fee),
        total=float(order.total_amount),
        payment_method=order.payment_method.value,
        notes=order.notes,
        rejection_reason=order.cancellation_reason if order.order_status == OrderStatus.REJECTED else None,
        delivery_address=DeliveryAddressOut(
            label=details.get("label"),
            line1=details.get("line1") or order.delivery_address,
            line2=details.get("line2"),
            city=details.get("city") or "",
            postal_code=details.get("postal_code"),
            latitude=order.delivery_latitude,
            longitude=order.delivery_longitude,
        ),
        vendor=OrderVendorBrief(
            id=vendor.id, name=vendor.shop_name, address=vendor.address, latitude=vendor.latitude,
            longitude=vendor.longitude, phone=vendor.phone, logo_url=vendor.logo_url,
        ),
        customer=to_user_brief(order.customer),
        rider=(
            OrderRiderBrief(
                id=rider.id, full_name=rider.user.name, phone=rider.user.phone,
                vehicle_type=app_vehicle(rider.vehicle_type), vehicle_number=rider.vehicle_number,
            )
            if rider is not None else None
        ),
        status_history=[
            StatusEventOut(status=app_status(entry.status), created_at=entry.created_at, note=entry.description)
            for entry in order.timeline
        ],
        distance_km=distance,
        rider_earning=_f(order.rider_earning),
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def to_app_rider(rider: Rider, *, total_deliveries: int = 0, private_user: bool = False) -> AppRider:
    return AppRider(
        id=rider.id,
        user_id=rider.user_id,
        vehicle_type=app_vehicle(rider.vehicle_type),
        vehicle_number=rider.vehicle_number,
        is_online=rider.availability != RiderAvailability.OFFLINE,
        approval_status=app_approval(rider.status),
        total_deliveries=total_deliveries,
        current_latitude=rider.latitude,
        current_longitude=rider.longitude,
        user=to_user_brief(rider.user, private=private_user),
        created_at=rider.created_at,
    )


def to_app_location(
    point: DeliveryCoordinate | RiderLocation, *, rider_name: str | None = None
) -> AppRiderLocation:
    updated_at: datetime = point.recorded_at if isinstance(point, DeliveryCoordinate) else point.updated_at
    return AppRiderLocation(
        rider_id=point.rider_id,
        order_id=point.order_id,
        latitude=point.latitude,
        longitude=point.longitude,
        heading=point.heading,
        speed=point.speed,
        accuracy=point.accuracy,
        updated_at=updated_at,
        rider_name=rider_name,
    )


def to_app_notification(n: Notification) -> AppNotification:
    return AppNotification(
        id=n.id, user_id=n.user_id, title=n.title, message=n.message, type=app_notification_type(n.type),
        is_read=n.is_read, order_id=n.order_id, created_at=n.created_at,
    )
