import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.product import Product
from app.repositories.base import BaseRepository


class ProductRepository(BaseRepository[Product]):
    model = Product

    async def get_many_with_vendor(self, ids: list[uuid.UUID]) -> list[Product]:
        stmt = select(Product).where(Product.id.in_(ids)).options(selectinload(Product.vendor))
        return await self.all(stmt)
