import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.enums import ACTIVE_ORDER_STATUSES
from app.models.order import Checkout, Order, OrderItem
from app.models.rider import Rider
from app.repositories.base import BaseRepository

ORDER_LIST_OPTIONS = (selectinload(Order.items).selectinload(OrderItem.product), selectinload(Order.vendor))
ORDER_DETAIL_OPTIONS = (
    selectinload(Order.items).selectinload(OrderItem.product),
    selectinload(Order.vendor),
    selectinload(Order.customer),
    selectinload(Order.timeline),
    selectinload(Order.rider).selectinload(Rider.user),
)
# Relationships needed by the status workflow (ownership checks, notifications).
ORDER_WORKFLOW_OPTIONS = (selectinload(Order.vendor), selectinload(Order.rider).selectinload(Rider.user))


class OrderRepository(BaseRepository[Order]):
    model = Order

    async def get_detail(self, order_id: uuid.UUID) -> Order | None:
        return await self.get(order_id, options=ORDER_DETAIL_OPTIONS)

    async def get_for_workflow(self, order_id: uuid.UUID) -> Order | None:
        """Lock the order row, then load the relationships the workflow needs."""
        order = await self.get(order_id, for_update=True)
        if order is None:
            return None
        return await self.get(order_id, options=ORDER_WORKFLOW_OPTIONS)

    async def get_checkout_by_key(self, customer_id: uuid.UUID, key: str) -> Checkout | None:
        stmt = select(Checkout).where(Checkout.customer_id == customer_id, Checkout.idempotency_key == key)
        return (await self.session.execute(stmt)).scalars().first()

    async def list_by_checkout(self, reference: str) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.checkout_reference == reference)
            .options(*ORDER_LIST_OPTIONS)
            .order_by(Order.created_at, Order.order_number)
        )
        return await self.all(stmt)

    async def count_active_for_rider(self, rider_id: uuid.UUID, *, exclude_order_id: uuid.UUID | None = None) -> int:
        stmt = select(func.count()).where(
            Order.rider_id == rider_id, Order.order_status.in_(ACTIVE_ORDER_STATUSES)
        )
        if exclude_order_id:
            stmt = stmt.where(Order.id != exclude_order_id)
        return int((await self.session.execute(stmt)).scalar_one())
