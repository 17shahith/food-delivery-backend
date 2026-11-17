import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from app.models.enums import OrderStatus, PaymentMethod

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.rider import Rider
    from app.models.user import User
    from app.models.vendor import Vendor


class Checkout(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Groups the vendor-specific orders created from one multi-vendor cart."""

    __tablename__ = "checkouts"
    __table_args__ = (
        UniqueConstraint("customer_id", "idempotency_key", name="uq_checkouts_customer_idempotency_key"),
    )

    reference: Mapped[str] = mapped_column(String(40), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    orders: Mapped[list["Order"]] = relationship(back_populates="checkout")


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="total_amount_non_negative"),
        Index("ix_orders_customer_created", "customer_id", "created_at"),
        Index("ix_orders_vendor_status", "vendor_id", "order_status"),
        Index("ix_orders_rider_status", "rider_id", "order_status"),
    )

    order_number: Mapped[str] = mapped_column(String(32), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"))
    rider_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("riders.id", ondelete="SET NULL"))
    checkout_reference: Mapped[str] = mapped_column(
        String(40), ForeignKey("checkouts.reference", ondelete="RESTRICT"), index=True
    )
    subtotal: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    payment_method: Mapped[PaymentMethod] = mapped_column(
        enum_type(PaymentMethod, "payment_method"), default=PaymentMethod.COD, server_default=PaymentMethod.COD.value
    )
    rider_earning: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    order_status: Mapped[OrderStatus] = mapped_column(
        enum_type(OrderStatus, "order_status"), default=OrderStatus.ORDER_PLACED, index=True
    )
    delivery_address: Mapped[str] = mapped_column(String(500))
    delivery_latitude: Mapped[float | None] = mapped_column(Float)
    delivery_longitude: Mapped[float | None] = mapped_column(Float)
    # Structured snapshot of the delivery address (label, line1, line2, city, postal_code).
    delivery_details: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(String(500))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))

    customer: Mapped["User"] = relationship()
    vendor: Mapped["Vendor"] = relationship()
    rider: Mapped[Optional["Rider"]] = relationship()
    checkout: Mapped["Checkout"] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    timeline: Mapped[list["StatusTimeline"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="StatusTimeline.created_at"
    )

    @property
    def vendor_name(self) -> str:
        return self.vendor.shop_name


class OrderItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    # SET NULL keeps order history intact if a product is later deleted; name/price are snapshotted.
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    product_name: Mapped[str] = mapped_column(String(150))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped[Optional["Product"]] = relationship(viewonly=True)

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


class StatusTimeline(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "status_timelines"
    __table_args__ = (Index("ix_status_timelines_order_created", "order_id", "created_at"),)

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    status: Mapped[OrderStatus] = mapped_column(enum_type(OrderStatus, "order_status"))
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    order: Mapped["Order"] = relationship(back_populates="timeline")
