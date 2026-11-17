import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import AdminUser, CurrentUser, PaginationDep, get_notification_service
from app.schemas.common import AUTH_ERRORS, MessageResponse, error_responses
from app.schemas.notification import NotificationCreate, NotificationCreateResult, NotificationList, NotificationOut
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications"])
Service = Annotated[NotificationService, Depends(get_notification_service)]


@router.get(
    "",
    response_model=NotificationList,
    responses=error_responses(*AUTH_ERRORS),
    summary="Own notifications (newest first)",
    description="New notifications are also pushed instantly on Realtime topic `user:{user_id}` "
    "(event `notification`) and via Postgres Changes on the `notifications` table.",
)
async def list_notifications(
    user: CurrentUser, service: Service, page: PaginationDep, is_read: bool | None = None
) -> NotificationList:
    items, total, unread = await service.list_for_user(user.id, is_read=is_read, limit=page.limit, offset=page.offset)
    return NotificationList(
        items=[NotificationOut.model_validate(n) for n in items],
        total=total, unread_count=unread, limit=page.limit, offset=page.offset,
    )


@router.post(
    "",
    response_model=NotificationCreateResult,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Send an announcement to a user or a whole role (admin)",
)
async def create_notification(payload: NotificationCreate, _: AdminUser, service: Service) -> NotificationCreateResult:
    return NotificationCreateResult(created=await service.create_announcement(payload))


@router.put(
    "/read-all",
    response_model=MessageResponse,
    responses=error_responses(*AUTH_ERRORS),
    summary="Mark all own notifications as read",
)
async def mark_all_read(user: CurrentUser, service: Service) -> MessageResponse:
    count = await service.mark_all_read(user.id)
    return MessageResponse(message=f"{count} notification(s) marked as read")


@router.get(
    "/{notification_id}",
    response_model=NotificationOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Get a notification",
)
async def get_notification(notification_id: uuid.UUID, user: CurrentUser, service: Service) -> NotificationOut:
    return NotificationOut.model_validate(await service.get_for_user(notification_id, user))


@router.put(
    "/{notification_id}/read",
    response_model=NotificationOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Mark a notification as read",
)
async def mark_read(notification_id: uuid.UUID, user: CurrentUser, service: Service) -> NotificationOut:
    return NotificationOut.model_validate(await service.mark_read(notification_id, user))
