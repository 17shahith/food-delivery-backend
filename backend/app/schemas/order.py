import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import OrderStatus, PaymentMethod
from app.schemas.common import Latitude, Longitude, Money, ORMModel
from app.schemas.rider import RiderSummary
from app.schemas.user import UserSummary
from app.schemas.vendor import VendorSummary


class OrderItemCreate(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=100)


class CheckoutCreate(BaseModel):
    items: list[OrderItemCreate] = Field(min_length=1, max_length=50)
    delivery_address: str = Field(min_length=5, max_length=500)
    delivery_latitude: Latitude | None = None
    delivery_longitude: Longitude | None = None
    notes: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _both_coordinates(self) -> "CheckoutCreate":
        if (self.delivery_latitude is None) != (self.delivery_longitude is None):
            raise ValueError("delivery_latitude and delivery_longitude must be provided together")
        return self


class OrderItemOut(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID | None
    product_name: str
    quantity: int
    unit_price: Money
    line_total: Money


class StatusTimelineOut(ORMModel):
    id: uuid.UUID
    status: OrderStatus
    description: str | None
    updated_by: uuid.UUID | None
    created_at: datetime


class OrderOut(ORMModel):
    id: uuid.UUID
    order_number: str
    checkout_reference: str
    customer_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str
    rider_id: uuid.UUID | None
    subtotal: Money
    delivery_fee: Money
    total_amount: Money
    payment_method: PaymentMethod
    rider_earning: Money | None
    order_status: OrderStatus
    delivery_address: str
    delivery_latitude: float | None
    delivery_longitude: float | None
    notes: str | None
    cancellation_reason: str | None
    items: list[OrderItemOut]
    created_at: datetime
    updated_at: datetime


class OrderDetailOut(OrderOut):
    vendor: VendorSummary
    customer: UserSummary
    rider: RiderSummary | None
    timeline: list[StatusTimelineOut]


class CheckoutResponse(BaseModel):
    checkout_reference: str
    total_amount: Money
    orders: list[OrderOut]


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
    description: str | None = Field(None, max_length=500)


class OrderCancelRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)
