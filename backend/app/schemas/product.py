import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Money, ORMModel

PriceField = Field(gt=0, max_digits=10, decimal_places=2, examples=[49.99])
ImageUrl = Field(None, max_length=500, pattern=r"^https?://\S+$")


class ProductCreate(BaseModel):
    vendor_id: uuid.UUID | None = Field(None, description="Admins only; vendors always create for their own shop")
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, max_length=80)
    price: Decimal = PriceField
    image_url: str | None = ImageUrl
    availability: bool = True
    stock: int | None = Field(None, ge=0, le=1_000_000, description="Tracked stock; null = not tracked")
    unit: str | None = Field(None, max_length=30, examples=["kg", "pack of 6"])


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = Field(None, max_length=2000)
    category: str | None = Field(None, max_length=80)
    price: Decimal | None = Field(None, gt=0, max_digits=10, decimal_places=2)
    image_url: str | None = ImageUrl
    availability: bool | None = None
    stock: int | None = Field(None, ge=0, le=1_000_000)
    unit: str | None = Field(None, max_length=30)


class ProductOut(ORMModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    name: str
    description: str | None
    category: str | None
    price: Money
    image_url: str | None
    availability: bool
    stock: int | None
    unit: str | None
    created_at: datetime
    updated_at: datetime


ProductSort = Literal["newest", "price_asc", "price_desc", "name"]
