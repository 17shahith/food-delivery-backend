import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, String, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.base import TimestampMixin, enum_type
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.rider import Rider
    from app.models.vendor import Vendor


class User(TimestampMixin, Base):
    """Application profile. `id` is the Supabase `auth.users.id` of the same person."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    role: Mapped[UserRole] = mapped_column(enum_type(UserRole, "user_role"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    vendor: Mapped[Optional["Vendor"]] = relationship(back_populates="user", uselist=False)
    rider: Mapped[Optional["Rider"]] = relationship(back_populates="user", uselist=False)
