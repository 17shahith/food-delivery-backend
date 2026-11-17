import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(
        self, id_: uuid.UUID, *, options: Sequence[Any] = (), for_update: bool = False
    ) -> ModelT | None:
        stmt = select(self.model).where(self.model.id == id_).options(*options)  # type: ignore[attr-defined]
        if for_update:
            # Row lock (no-op on SQLite) serialises concurrent state changes on Postgres.
            stmt = stmt.with_for_update(of=self.model)
        return (await self.session.execute(stmt)).scalars().first()

    def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)

    async def paginate(self, stmt: Select, limit: int, offset: int) -> tuple[list[Any], int]:
        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self.session.execute(count_stmt)).scalar_one()
        rows = (await self.session.execute(stmt.limit(limit).offset(offset))).scalars().unique().all()
        return list(rows), int(total)

    async def all(self, stmt: Select) -> list[Any]:
        return list((await self.session.execute(stmt)).scalars().unique().all())
