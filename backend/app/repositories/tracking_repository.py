import uuid

from sqlalchemy import func, select

from app.models.tracking import DeliveryCoordinate
from app.repositories.base import BaseRepository


class TrackingRepository(BaseRepository[DeliveryCoordinate]):
    model = DeliveryCoordinate

    async def latest_for_order(self, order_id: uuid.UUID) -> DeliveryCoordinate | None:
        stmt = (
            select(DeliveryCoordinate)
            .where(DeliveryCoordinate.order_id == order_id)
            .order_by(DeliveryCoordinate.recorded_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def latest_for_orders(self, order_ids: list[uuid.UUID]) -> dict[uuid.UUID, DeliveryCoordinate]:
        if not order_ids:
            return {}
        latest = (
            select(DeliveryCoordinate.order_id, func.max(DeliveryCoordinate.recorded_at).label("max_at"))
            .where(DeliveryCoordinate.order_id.in_(order_ids))
            .group_by(DeliveryCoordinate.order_id)
            .subquery()
        )
        stmt = select(DeliveryCoordinate).join(
            latest,
            (DeliveryCoordinate.order_id == latest.c.order_id)
            & (DeliveryCoordinate.recorded_at == latest.c.max_at),
        )
        return {point.order_id: point for point in await self.all(stmt)}

    async def history(self, order_id: uuid.UUID, limit: int) -> tuple[list[DeliveryCoordinate], int]:
        stmt = (
            select(DeliveryCoordinate)
            .where(DeliveryCoordinate.order_id == order_id)
            .order_by(DeliveryCoordinate.recorded_at.asc())
        )
        return await self.paginate(stmt, limit, 0)
