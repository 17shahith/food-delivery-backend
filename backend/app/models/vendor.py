import uuid
from typing import TYPE_CHECKING

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, Numeric, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from app.models.enums import ApprovalStatus

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.user import User


class Vendor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vendors"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("delivery_fee >= 0", name="delivery_fee_non_negative"),
        Index("ix_vendors_location", "latitude", "longitude"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    shop_name: Mapped[str] = mapped_column(String(150), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING, index=True
    )
    is_open: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    category: Mapped[str] = mapped_column(String(80), default="general", server_default="general", index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    logo_url: Mapped[str | None] = mapped_column(String(500))
    banner_url: Mapped[str | None] = mapped_column(String(500))
    opening_hours: Mapped[str | None] = mapped_column(String(120))
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")
    min_order_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    avg_prep_minutes: Mapped[int | None] = mapped_column(Integer)

    user: Mapped["User"] = relationship(back_populates="vendor")
    products: Mapped[list["Product"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan", passive_deletes=True
    )
