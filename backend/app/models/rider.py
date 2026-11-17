import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.base import CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_type
from app.models.enums import ApprovalStatus, AssignmentStatus, RiderAvailability, VehicleType

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.user import User


class Rider(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "riders"
    __table_args__ = (
        CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude IS NULL OR longitude BETWEEN -180 AND 180", name="longitude_range"),
        Index("ix_riders_status_availability", "status", "availability"),
        Index("ix_riders_location", "latitude", "longitude"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    vehicle_type: Mapped[VehicleType] = mapped_column(enum_type(VehicleType, "vehicle_type"))
    vehicle_number: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "approval_status"), default=ApprovalStatus.PENDING
    )
    availability: Mapped[RiderAvailability] = mapped_column(
        enum_type(RiderAvailability, "rider_availability"), default=RiderAvailability.OFFLINE
    )
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    last_location_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="rider")

    @property
    def name(self) -> str:
        return self.user.name

    @property
    def phone(self) -> str | None:
        return self.user.phone


class RiderAssignment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A delivery offer to a rider, which the rider accepts or rejects."""

    __tablename__ = "rider_assignments"
    __table_args__ = (
        # At most one live (pending/accepted) assignment per order - prevents double assignment.
        Index(
            "uq_rider_assignments_active_order",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'accepted')"),
            sqlite_where=text("status IN ('pending', 'accepted')"),
        ),
        Index("ix_rider_assignments_rider_status", "rider_id", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    rider_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("riders.id", ondelete="CASCADE"))
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[AssignmentStatus] = mapped_column(
        enum_type(AssignmentStatus, "assignment_status"), default=AssignmentStatus.PENDING
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped["Order"] = relationship()
    rider: Mapped[Optional["Rider"]] = relationship()
