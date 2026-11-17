import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.enums import AssignmentStatus
from app.models.rider import Rider, RiderAssignment
from app.repositories.base import BaseRepository

RIDER_OPTIONS = (selectinload(Rider.user),)


class RiderRepository(BaseRepository[Rider]):
    model = Rider

    async def get_with_user(self, rider_id: uuid.UUID, *, for_update: bool = False) -> Rider | None:
        if for_update:
            await self.get(rider_id, for_update=True)
        return await self.get(rider_id, options=RIDER_OPTIONS)

    async def get_by_user_id(self, user_id: uuid.UUID) -> Rider | None:
        stmt = select(Rider).where(Rider.user_id == user_id).options(*RIDER_OPTIONS)
        return (await self.session.execute(stmt)).scalars().first()


class AssignmentRepository(BaseRepository[RiderAssignment]):
    model = RiderAssignment

    async def get_active_for_order(self, order_id: uuid.UUID) -> RiderAssignment | None:
        stmt = select(RiderAssignment).where(
            RiderAssignment.order_id == order_id,
            RiderAssignment.status.in_([AssignmentStatus.PENDING, AssignmentStatus.ACCEPTED]),
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def list_pending_for_rider(self, rider_id: uuid.UUID) -> list[RiderAssignment]:
        stmt = select(RiderAssignment).where(
            RiderAssignment.rider_id == rider_id, RiderAssignment.status == AssignmentStatus.PENDING
        )
        return await self.all(stmt)

    def for_rider(self, rider_id: uuid.UUID, status: AssignmentStatus | None):
        stmt = select(RiderAssignment).where(RiderAssignment.rider_id == rider_id)
        if status:
            stmt = stmt.where(RiderAssignment.status == status)
        return stmt.order_by(RiderAssignment.created_at.desc())
