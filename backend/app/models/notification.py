import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base
from app.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(150))
    message: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(50), default="general")
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
