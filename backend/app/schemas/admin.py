import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import OrderStatus, UserRole
from app.schemas.common import Money
from app.schemas.rider import RiderSummary
from app.schemas.tracking import TrackingPoint


class AdminUserUpdate(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class TopVendor(BaseModel):
    vendor_id: uuid.UUID
    shop_name: str
    delivered_orders: int
    revenue: Money


class PlatformAnalytics(BaseModel):
    users_by_role: dict[str, int]
    vendors_by_status: dict[str, int]
    riders_by_availability: dict[str, int]
    orders_by_status: dict[str, int]
    total_orders: int
    orders_today: int
    active_deliveries: int
    delivered_revenue: Money
    average_order_value: Money
    average_delivery_minutes: float | None
    top_vendors: list[TopVendor]
    generated_at: datetime


class LiveDelivery(BaseModel):
    order_id: uuid.UUID
    order_number: str
    order_status: OrderStatus
    vendor_name: str
    delivery_address: str
    rider: RiderSummary | None
    current_location: TrackingPoint | None
    updated_at: datetime
