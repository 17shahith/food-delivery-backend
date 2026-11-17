"""Schemas of the web-app API (`/api/app`).

They mirror the React frontend's TypeScript types (`src/types/index.ts`): `full_name`,
`is_available`, lowercase order statuses, `status_history`, structured `delivery_address`, etc.
Mapping to/from the domain model lives in `app/services/client_mappers.py`.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.schemas.common import Latitude, Longitude, Phone

AppRole = Literal["customer", "vendor", "rider", "admin"]
AppApproval = Literal["pending", "approved", "rejected"]
AppVehicle = Literal["bicycle", "motorbike", "scooter", "car", "on_foot"]
AppOrderStatus = Literal[
    "placed", "accepted", "preparing", "ready_for_pickup", "rider_assigned",
    "picked_up", "out_for_delivery", "delivered", "rejected", "cancelled",
]
AppNotificationType = Literal["order", "delivery", "system", "approval", "promo"]
OptionalUrl = Field(None, max_length=500)


def _blank_to_none(value):
    return None if isinstance(value, str) and not value.strip() else value


def _check_url(value: str | None) -> str | None:
    if value is not None and not value.startswith(("https://", "http://")):
        raise ValueError("must be an http(s) URL")
    return value


# ----------------------------------------------------------------------------- users & auth
class AppUser(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    phone: str | None
    role: AppRole
    avatar_url: str | None
    is_active: bool
    approval_status: AppApproval | None
    created_at: datetime


class UserBrief(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None
    phone: str | None = None
    is_active: bool | None = None


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str | None
    token_type: str = "bearer"
    user: AppUser


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str | None


class MessageOut(BaseModel):
    message: str


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=120)
    phone: Phone | None = None
    role: Literal["customer", "vendor", "rider"]
    store_name: str | None = Field(None, min_length=2, max_length=150)
    store_category: str | None = Field(None, max_length=80)
    store_address: str | None = Field(None, min_length=5, max_length=500)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    vehicle_type: AppVehicle | None = None
    vehicle_number: str | None = Field(None, max_length=30)

    _blank = field_validator("phone", "store_category", "vehicle_number", mode="before")(_blank_to_none)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def _role_fields(self) -> "RegisterIn":
        if self.role == "vendor":
            missing = [f for f in ("store_name", "store_address", "latitude", "longitude") if getattr(self, f) is None]
            if missing:
                raise ValueError(f"Vendors must provide: {', '.join(missing)}")
        if self.role == "rider" and self.vehicle_type is None:
            raise ValueError("Riders must provide vehicle_type")
        return self


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1)


class ProfilePatch(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=120)
    phone: Phone | None = None
    avatar_url: str | None = OptionalUrl

    _blank = field_validator("phone", "avatar_url", mode="before")(_blank_to_none)
    _url = field_validator("avatar_url")(_check_url)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordIn(BaseModel):
    email: EmailStr
    redirect_to: str | None = Field(None, max_length=500)


# ----------------------------------------------------------------------------- addresses
class AddressIn(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    line1: str = Field(min_length=2, max_length=200)
    line2: str | None = Field(None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str = Field(min_length=3, max_length=20)
    latitude: Latitude
    longitude: Longitude
    is_default: bool = False

    _blank = field_validator("line2", "state", mode="before")(_blank_to_none)


class AddressPatch(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=50)
    line1: str | None = Field(None, min_length=2, max_length=200)
    line2: str | None = Field(None, max_length=200)
    city: str | None = Field(None, min_length=1, max_length=100)
    state: str | None = Field(None, max_length=100)
    postal_code: str | None = Field(None, min_length=3, max_length=20)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    is_default: bool | None = None


class AddressOut(BaseModel):
    id: uuid.UUID
    label: str
    line1: str
    line2: str | None
    city: str
    state: str | None
    postal_code: str
    latitude: float
    longitude: float
    is_default: bool


# ----------------------------------------------------------------------------- vendors & products
class AppVendor(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    description: str | None
    category: str
    logo_url: str | None
    banner_url: str | None
    address: str
    phone: str | None
    rating: float | None = None
    rating_count: int | None = None
    is_open: bool
    approval_status: AppApproval
    opening_hours: str | None
    distance_km: float | None = None
    delivery_fee: float
    min_order_amount: float | None
    avg_prep_minutes: int | None
    latitude: float
    longitude: float
    owner: UserBrief | None = None
    created_at: datetime


class VendorPatch(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=150)
    description: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, min_length=1, max_length=80)
    logo_url: str | None = OptionalUrl
    banner_url: str | None = OptionalUrl
    address: str | None = Field(None, min_length=5, max_length=500)
    phone: Phone | None = None
    is_open: bool | None = None
    opening_hours: str | None = Field(None, max_length=120)
    delivery_fee: float | None = Field(None, ge=0, le=100000)
    min_order_amount: float | None = Field(None, ge=0, le=1000000)
    avg_prep_minutes: int | None = Field(None, ge=0, le=600)
    latitude: Latitude | None = None
    longitude: Longitude | None = None

    _blank = field_validator("phone", "logo_url", "banner_url", "description", "opening_hours", mode="before")(
        _blank_to_none
    )
    _url = field_validator("logo_url", "banner_url")(_check_url)


class VendorStatsOut(BaseModel):
    orders_today: int
    revenue_today: float
    pending_orders: int
    active_orders: int
    total_products: int
    completed_orders: int


class ProductVendorBrief(BaseModel):
    id: uuid.UUID
    name: str
    is_open: bool
    delivery_fee: float


class AppProduct(BaseModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    name: str
    description: str | None
    price: float
    category: str
    image_url: str | None
    is_available: bool
    stock: int | None
    unit: str | None
    vendor: ProductVendorBrief | None = None
    created_at: datetime


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(None, max_length=2000)
    price: float = Field(gt=0, le=10_000_000)
    category: str = Field(min_length=1, max_length=80)
    image_url: str | None = OptionalUrl
    is_available: bool = True
    stock: int | None = Field(None, ge=0, le=1_000_000)
    unit: str | None = Field(None, max_length=30)

    _blank = field_validator("description", "image_url", "unit", mode="before")(_blank_to_none)
    _url = field_validator("image_url")(_check_url)


class ProductPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = Field(None, max_length=2000)
    price: float | None = Field(None, gt=0, le=10_000_000)
    category: str | None = Field(None, min_length=1, max_length=80)
    image_url: str | None = OptionalUrl
    is_available: bool | None = None
    stock: int | None = Field(None, ge=0, le=1_000_000)
    unit: str | None = Field(None, max_length=30)

    _blank = field_validator("description", "image_url", "unit", mode="before")(_blank_to_none)
    _url = field_validator("image_url")(_check_url)


class AvailabilityIn(BaseModel):
    is_available: bool


class UploadOut(BaseModel):
    url: str


# ----------------------------------------------------------------------------- orders
class OrderLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=99)


class OrderCreateIn(BaseModel):
    vendor_id: uuid.UUID
    address_id: uuid.UUID
    items: list[OrderLineIn] = Field(min_length=1, max_length=50)
    payment_method: Literal["cod", "online"] = "cod"
    notes: str | None = Field(None, max_length=500)

    _blank = field_validator("notes", mode="before")(_blank_to_none)


class AppOrderItem(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID | None
    product_name: str
    quantity: int
    unit_price: float
    subtotal: float
    image_url: str | None


class StatusEventOut(BaseModel):
    status: AppOrderStatus
    created_at: datetime
    note: str | None


class DeliveryAddressOut(BaseModel):
    label: str | None
    line1: str
    line2: str | None
    city: str
    postal_code: str | None
    latitude: float | None
    longitude: float | None


class OrderVendorBrief(BaseModel):
    id: uuid.UUID
    name: str
    address: str
    latitude: float
    longitude: float
    phone: str | None
    logo_url: str | None


class OrderRiderBrief(BaseModel):
    id: uuid.UUID
    full_name: str
    phone: str | None
    vehicle_type: str | None
    vehicle_number: str | None


class AppOrder(BaseModel):
    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    vendor_id: uuid.UUID
    rider_id: uuid.UUID | None
    status: AppOrderStatus
    items: list[AppOrderItem]
    subtotal: float
    delivery_fee: float
    total: float
    payment_method: Literal["cod", "online"]
    notes: str | None
    rejection_reason: str | None
    delivery_address: DeliveryAddressOut
    vendor: OrderVendorBrief | None
    customer: UserBrief | None
    rider: OrderRiderBrief | None
    status_history: list[StatusEventOut]
    distance_km: float | None
    rider_earning: float | None
    created_at: datetime
    updated_at: datetime


class ReasonIn(BaseModel):
    reason: str | None = Field(None, max_length=500)


class OrderStatusIn(BaseModel):
    status: AppOrderStatus


# ----------------------------------------------------------------------------- riders & tracking
class AppRider(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    vehicle_type: AppVehicle
    vehicle_number: str | None
    is_online: bool
    approval_status: AppApproval
    rating: float | None = None
    total_deliveries: int
    current_latitude: float | None
    current_longitude: float | None
    user: UserBrief | None = None
    created_at: datetime


class RiderPatch(BaseModel):
    vehicle_type: AppVehicle | None = None
    vehicle_number: str | None = Field(None, max_length=30)


class OnlineIn(BaseModel):
    is_online: bool


class RiderStatsOut(BaseModel):
    deliveries_today: int
    earnings_today: float
    total_deliveries: int
    rating: float | None = None


class AppRiderLocation(BaseModel):
    rider_id: uuid.UUID
    order_id: uuid.UUID | None
    latitude: float
    longitude: float
    heading: float | None
    speed: float | None
    accuracy: float | None
    updated_at: datetime
    rider_name: str | None = None


# ----------------------------------------------------------------------------- notifications & admin
class AppNotification(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    message: str
    type: AppNotificationType
    is_read: bool
    order_id: uuid.UUID | None
    created_at: datetime


class ActiveIn(BaseModel):
    is_active: bool


class AdminStatsOut(BaseModel):
    total_customers: int
    total_vendors: int
    total_riders: int
    active_orders: int
    orders_today: int
    revenue_today: float
    pending_approvals: int
    online_riders: int


class DailyPoint(BaseModel):
    date: str
    orders: int
    revenue: float


class StatusCount(BaseModel):
    status: AppOrderStatus
    count: int


class TopVendorOut(BaseModel):
    vendor_id: uuid.UUID
    name: str
    orders: int
    revenue: float


class AnalyticsOut(BaseModel):
    daily: list[DailyPoint]
    status_breakdown: list[StatusCount]
    top_vendors: list[TopVendorOut]
    avg_delivery_minutes: float | None
