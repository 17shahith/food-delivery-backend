import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base
from app.models.base import UUIDPrimaryKeyMixin
from app.utils.helpers import utcnow


class DeliveryCoordinate(UUIDPrimaryKeyMixin, Base):
    """Persisted (throttled) GPS breadcrumb for an order's delivery route."""

    __tablename__ = "delivery_coordinates"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        Index("ix_delivery_coordinates_order_recorded", "order_id", "recorded_at"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("riders.id", ondelete="CASCADE"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    accuracy: Mapped[float | None] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class RiderLocation(Base):
    """Latest known position per rider (one row, upserted).

    Published through Supabase Realtime (Postgres Changes) so the web app can follow a rider
    with `filter: order_id=eq.<id>`; RLS limits rows to the rider, the order's parties and admins.
    """

    __tablename__ = "rider_locations"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
    )

    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("riders.id", ondelete="CASCADE"), primary_key=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    heading: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    accuracy: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
