import uuid
from decimal import Decimal

from sqlalchemy import or_, select

from app.models.enums import ApprovalStatus, UserRole
from app.models.product import Product
from app.models.user import User
from app.models.vendor import Vendor
from app.repositories.product_repository import ProductRepository
from app.repositories.vendor_repository import VendorRepository
from app.schemas.product import ProductCreate, ProductSort, ProductUpdate
from app.services.base import BaseService
from app.utils.exceptions import BadRequestError, NotFoundError, PermissionDeniedError
from app.utils.helpers import escape_like

SORTS = {
    "newest": Product.created_at.desc(),
    "price_asc": Product.price.asc(),
    "price_desc": Product.price.desc(),
    "name": Product.name.asc(),
}


class ProductService(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.repo = ProductRepository(session)
        self.vendors = VendorRepository(session)

    async def _resolve_vendor_for_write(self, user: User, vendor_id: uuid.UUID | None) -> Vendor:
        if user.role == UserRole.ADMIN:
            if vendor_id is None:
                raise BadRequestError("vendor_id is required when an administrator creates a product")
            vendor = await self.vendors.get(vendor_id)
            if vendor is None:
                raise NotFoundError("Vendor not found")
            return vendor
        vendor = await self.vendors.get_by_user_id(user.id)
        if vendor is None:
            raise NotFoundError("Create your shop before adding products", code="VENDOR_PROFILE_NOT_FOUND")
        if vendor_id not in (None, vendor.id):
            raise PermissionDeniedError("You can only add products to your own shop")
        if vendor.status in (ApprovalStatus.REJECTED, ApprovalStatus.SUSPENDED):
            raise PermissionDeniedError("Your shop is not allowed to manage products", code="VENDOR_NOT_ACTIVE")
        return vendor

    async def _get_managed(self, product_id: uuid.UUID, user: User) -> Product:
        product = await self.repo.get(product_id, options=())
        if product is None:
            raise NotFoundError("Product not found")
        if user.role != UserRole.ADMIN:
            vendor = await self.vendors.get_by_user_id(user.id)
            if vendor is None or vendor.id != product.vendor_id:
                raise PermissionDeniedError("You can only manage your own products")
        return product

    async def create(self, user: User, data: ProductCreate) -> Product:
        vendor = await self._resolve_vendor_for_write(user, data.vendor_id)
        product = Product(vendor_id=vendor.id, **data.model_dump(exclude={"vendor_id"}))
        self.repo.add(product)
        await self.commit()
        return product

    async def list_products(
        self,
        viewer: User | None,
        *,
        vendor_id: uuid.UUID | None,
        q: str | None,
        category: str | None,
        min_price: Decimal | None,
        max_price: Decimal | None,
        available: bool | None,
        sort: ProductSort,
        limit: int,
        offset: int,
    ) -> tuple[list[Product], int]:
        if min_price is not None and max_price is not None and min_price > max_price:
            raise BadRequestError("min_price cannot be greater than max_price")
        stmt = select(Product).join(Product.vendor)
        if not await self._can_see_unapproved(viewer, vendor_id):
            stmt = stmt.where(Vendor.status == ApprovalStatus.APPROVED)
        if vendor_id:
            stmt = stmt.where(Product.vendor_id == vendor_id)
        if q:
            pattern = f"%{escape_like(q)}%"
            stmt = stmt.where(
                or_(Product.name.ilike(pattern, escape="\\"), Product.description.ilike(pattern, escape="\\"))
            )
        if category:
            stmt = stmt.where(Product.category.ilike(escape_like(category), escape="\\"))
        if min_price is not None:
            stmt = stmt.where(Product.price >= min_price)
        if max_price is not None:
            stmt = stmt.where(Product.price <= max_price)
        if available is not None:
            stmt = stmt.where(Product.availability.is_(available))
        stmt = stmt.order_by(SORTS[sort], Product.id)
        return await self.repo.paginate(stmt, limit, offset)

    async def _can_see_unapproved(self, viewer: User | None, vendor_id: uuid.UUID | None) -> bool:
        if viewer is None:
            return False
        if viewer.role == UserRole.ADMIN:
            return True
        if viewer.role == UserRole.VENDOR and vendor_id is not None:
            own = await self.vendors.get_by_user_id(viewer.id)
            return own is not None and own.id == vendor_id
        return False

    async def get(self, product_id: uuid.UUID, viewer: User | None) -> Product:
        product = await self.repo.get(product_id)
        if product is None:
            raise NotFoundError("Product not found")
        vendor = await self.vendors.get(product.vendor_id)
        if vendor.status != ApprovalStatus.APPROVED and not await self._can_see_unapproved(viewer, vendor.id):
            raise NotFoundError("Product not found")
        return product

    async def update(self, product_id: uuid.UUID, user: User, data: ProductUpdate) -> Product:
        product = await self._get_managed(product_id, user)
        for field, value in data.model_dump(exclude_unset=True).items():
            if value is None and field in {"name", "price", "availability"}:
                continue  # required columns cannot be cleared
            setattr(product, field, value)
        await self.commit()
        return product

    async def delete(self, product_id: uuid.UUID, user: User) -> None:
        product = await self._get_managed(product_id, user)
        await self.repo.delete(product)
        await self.commit()
