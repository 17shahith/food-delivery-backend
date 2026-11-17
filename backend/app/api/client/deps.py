from typing import Annotated

from fastapi import Depends

from app.api.deps import DbSession, Publisher
from app.models.enums import ApprovalStatus, OrderStatus, UserRole
from app.models.order import Order
from app.models.user import User
from app.services.client_service import ClientService
from app.services.order_workflow import can_view_order
from app.services.storage_service import StorageService, get_storage_service
from app.utils.exceptions import NotFoundError


def get_client_service(db: DbSession, publisher: Publisher) -> ClientService:
    return ClientService(db, publisher)


Client = Annotated[ClientService, Depends(get_client_service)]
Storage = Annotated[StorageService, Depends(get_storage_service)]


async def ensure_app_order_access(client: ClientService, order: Order, user: User) -> None:
    """Order parties and admins; approved riders may also preview unassigned READY_FOR_PICKUP orders."""
    if can_view_order(order, user):
        return
    if user.role == UserRole.RIDER and order.order_status == OrderStatus.READY_FOR_PICKUP and order.rider_id is None:
        if await client.approval_for(user) == ApprovalStatus.APPROVED:
            return
    raise NotFoundError("Order not found")
