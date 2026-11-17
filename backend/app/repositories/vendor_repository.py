import uuid

from sqlalchemy import exists, select

from app.models.order import Order
from app.models.vendor import Vendor
from app.repositories.base import BaseRepository


class VendorRepository(BaseRepository[Vendor]):
    model = Vendor

    async def get_by_user_id(self, user_id: uuid.UUID) -> Vendor | None:
        result = await self.session.execute(select(Vendor).where(Vendor.user_id == user_id))
        return result.scalars().first()

    async def has_orders(self, vendor_id: uuid.UUID) -> bool:
        return bool(await self.session.scalar(select(exists().where(Order.vendor_id == vendor_id))))
