import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.vendor import Vendor


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("stock IS NULL OR stock >= 0", name="stock_non_negative"),
        Index("ix_products_vendor_availability", "vendor_id", "availability"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(150), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(80), index=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    image_url: Mapped[str | None] = mapped_column(String(500))
    availability: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    stock: Mapped[int | None] = mapped_column(Integer)
    unit: Mapped[str | None] = mapped_column(String(30))

    vendor: Mapped["Vendor"] = relationship(back_populates="products")
