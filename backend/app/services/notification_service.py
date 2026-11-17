import uuid

from app.models.enums import OrderStatus, UserRole
from app.models.notification import Notification
from app.repositories.notification_repository import NotificationRepository
from app.repositories.user_repository import UserRepository
from app.schemas.notification import NotificationCreate, NotificationOut
from app.services.base import BaseService
from app.services.realtime_service import user_topic
from app.utils.exceptions import NotFoundError, PermissionDeniedError

# Customer-facing messages per order status: (title, message template).
CUSTOMER_STATUS_MESSAGES: dict[OrderStatus, tuple[str, str]] = {
    OrderStatus.ORDER_PLACED: ("Order placed", "Your order {number} from {shop} has been placed."),
    OrderStatus.VENDOR_ACCEPTED: ("Order accepted", "{shop} accepted your order {number}."),
    OrderStatus.PREPARING: ("Preparing your order", "{shop} is preparing your order {number}."),
    OrderStatus.READY_FOR_PICKUP: ("Ready for pickup", "Your order {number} is packed and waiting for a rider."),
    OrderStatus.RIDER_ASSIGNED: ("Rider assigned", "{rider} will deliver your order {number}."),
    OrderStatus.PICKED_UP: ("Order picked up", "{rider} has picked up your order {number}."),
    OrderStatus.OUT_FOR_DELIVERY: ("Out for delivery", "Your order {number} is on its way."),
    OrderStatus.DELIVERED: ("Order delivered", "Your order {number} has been delivered. Enjoy!"),
    OrderStatus.REJECTED: ("Order rejected", "{shop} could not accept your order {number}."),
    OrderStatus.CANCELLED: ("Order cancelled", "Your order {number} was cancelled."),
}


class NotificationService(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.repo = NotificationRepository(session)
        self.users = UserRepository(session)

    # -- creation (used by other services inside their transaction) ---------------------------
    def notify(
        self,
        user_id: uuid.UUID,
        title: str,
        message: str,
        *,
        type_: str = "general",
        order_id: uuid.UUID | None = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id, title=title, message=message, type=type_, order_id=order_id, is_read=False
        )
        self.repo.add(notification)
        self.queue_event(
            user_topic(user_id),
            "notification",
            lambda: NotificationOut.model_validate(notification).model_dump(),
        )
        return notification

    async def notify_role(self, role: UserRole, title: str, message: str, *, type_: str = "general") -> int:
        ids = await self.users.active_ids_by_role(role)
        for user_id in ids:
            self.notify(user_id, title, message, type_=type_)
        return len(ids)

    # -- API operations ---------------------------------------------------------------------------
    async def list_for_user(self, user_id: uuid.UUID, *, is_read: bool | None, limit: int, offset: int):
        items, total = await self.repo.paginate(self.repo.for_user(user_id, is_read), limit, offset)
        unread = await self.repo.unread_count(user_id)
        return items, total, unread

    async def get_for_user(self, notification_id: uuid.UUID, user) -> Notification:
        notification = await self.repo.get(notification_id)
        if notification is None:
            raise NotFoundError("Notification not found")
        if notification.user_id != user.id and user.role != UserRole.ADMIN:
            # Do not reveal other users' notifications exist.
            raise NotFoundError("Notification not found")
        return notification

    async def mark_read(self, notification_id: uuid.UUID, user) -> Notification:
        notification = await self.get_for_user(notification_id, user)
        if notification.user_id != user.id:
            raise PermissionDeniedError("Only the recipient can mark a notification as read")
        notification.is_read = True
        await self.commit()
        return notification

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        count = await self.repo.mark_all_read(user_id)
        await self.commit()
        return count

    async def create_announcement(self, data: NotificationCreate) -> int:
        if data.user_id is not None:
            if await self.users.get(data.user_id) is None:
                raise NotFoundError("User not found")
            self.notify(data.user_id, data.title, data.message, type_="announcement")
            created = 1
        else:
            created = await self.notify_role(data.role, data.title, data.message, type_="announcement")
        await self.commit()
        return created
