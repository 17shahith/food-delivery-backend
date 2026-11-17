import uuid

from sqlalchemy import func, select, update

from app.models.notification import Notification
from app.repositories.base import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    model = Notification

    def for_user(self, user_id: uuid.UUID, is_read: bool | None):
        stmt = select(Notification).where(Notification.user_id == user_id)
        if is_read is not None:
            stmt = stmt.where(Notification.is_read.is_(is_read))
        return stmt.order_by(Notification.created_at.desc())

    async def unread_count(self, user_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(Notification.user_id == user_id, Notification.is_read.is_(False))
        return int((await self.session.execute(stmt)).scalar_one())

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.is_read.is_(False))
            .values(is_read=True)
        )
        return result.rowcount or 0
