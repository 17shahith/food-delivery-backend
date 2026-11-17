import uuid
from datetime import datetime

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.enums import ApprovalStatus
from app.schemas.common import Latitude, Longitude, Money, ORMModel, Phone

UrlStr = Field(None, max_length=500, pattern=r"^https?://\S+$")


class VendorShopDetails(BaseModel):
    shop_name: str = Field(min_length=2, max_length=150, examples=["Green Basket Grocers"])
    description: str | None = Field(None, max_length=2000)
    address: str = Field(min_length=5, max_length=500)
    latitude: Latitude
    longitude: Longitude
    category: str = Field("general", min_length=1, max_length=80, examples=["grocery"])


class VendorCreate(VendorShopDetails):
    user_id: uuid.UUID | None = Field(
        None, description="Admins only: create the shop on behalf of an existing vendor user"
    )


class VendorUpdate(BaseModel):
    shop_name: str | None = Field(None, min_length=2, max_length=150)
    description: str | None = Field(None, max_length=2000)
    address: str | None = Field(None, min_length=5, max_length=500)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    category: str | None = Field(None, min_length=1, max_length=80)
    phone: Phone | None = None
    logo_url: str | None = UrlStr
    banner_url: str | None = UrlStr
    opening_hours: str | None = Field(None, max_length=120)
    delivery_fee: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    min_order_amount: Decimal | None = Field(None, ge=0, max_digits=10, decimal_places=2)
    avg_prep_minutes: int | None = Field(None, ge=0, le=600)


class VendorStatusUpdate(BaseModel):
    status: ApprovalStatus
    reason: str | None = Field(None, max_length=500)


class VendorAvailabilityUpdate(BaseModel):
    is_open: bool


class VendorOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    shop_name: str
    description: str | None
    address: str
    latitude: float
    longitude: float
    status: ApprovalStatus
    is_open: bool
    category: str
    phone: str | None
    logo_url: str | None
    banner_url: str | None
    opening_hours: str | None
    delivery_fee: Money
    min_order_amount: Money | None
    avg_prep_minutes: int | None
    created_at: datetime
    updated_at: datetime
    distance_km: float | None = Field(None, description="Present for nearby searches")


class VendorSummary(ORMModel):
    id: uuid.UUID
    shop_name: str
    address: str
    latitude: float
    longitude: float
