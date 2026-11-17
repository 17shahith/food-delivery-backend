import uuid

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class CustomerAddress(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Saved delivery address of a customer (address book)."""

    __tablename__ = "customer_addresses"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        Index("ix_customer_addresses_user_default", "user_id", "is_default"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(50))
    line1: Mapped[str] = mapped_column(String(200))
    line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(100))
    postal_code: Mapped[str] = mapped_column(String(20))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    def formatted(self) -> str:
        parts = [self.line1, self.line2, self.city, self.state, self.postal_code]
        return ", ".join(p for p in parts if p)

    def snapshot(self) -> dict:
        """Copy stored on the order so later edits to the address book don't rewrite history."""
        return {
            "label": self.label,
            "line1": self.line1,
            "line2": self.line2,
            "city": self.city,
            "postal_code": self.postal_code,
        }
