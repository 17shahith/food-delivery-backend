import uuid

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.models.enums import ApprovalStatus, UserRole
from app.models.user import User
from app.models.vendor import Vendor
from app.repositories.user_repository import UserRepository
from app.repositories.vendor_repository import VendorRepository
from app.schemas.vendor import VendorCreate, VendorOut, VendorStatusUpdate, VendorUpdate
from app.services.base import BaseService
from app.services.notification_service import NotificationService
from app.utils.exceptions import BadRequestError, ConflictError, NotFoundError, PermissionDeniedError
from app.utils.helpers import bounding_box, escape_like, haversine_km

VENDOR_STATUS_MESSAGES = {
    ApprovalStatus.APPROVED: ("Shop approved", "{shop} is approved. You can now open your shop and accept orders."),
    ApprovalStatus.REJECTED: ("Shop rejected", "{shop} was not approved.{reason}"),
    ApprovalStatus.SUSPENDED: ("Shop suspended", "{shop} has been suspended.{reason}"),
    ApprovalStatus.PENDING: ("Shop under review", "{shop} is pending review."),
}


class VendorService(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.repo = VendorRepository(session)
        self.users = UserRepository(session)
        self.notifications = NotificationService(session, publisher)

    @staticmethod
    def _is_owner_or_admin(vendor: Vendor, user: User | None) -> bool:
        return user is not None and (user.role == UserRole.ADMIN or vendor.user_id == user.id)

    async def _get_or_404(self, vendor_id: uuid.UUID) -> Vendor:
        vendor = await self.repo.get(vendor_id)
        if vendor is None:
            raise NotFoundError("Vendor not found")
        return vendor

    async def _get_managed(self, vendor_id: uuid.UUID, user: User) -> Vendor:
        vendor = await self._get_or_404(vendor_id)
        if not self._is_owner_or_admin(vendor, user):
            raise PermissionDeniedError("You can only manage your own shop")
        return vendor

    async def create(self, user: User, data: VendorCreate) -> Vendor:
        if user.role == UserRole.ADMIN:
            if data.user_id is None:
                raise BadRequestError("user_id is required when an administrator creates a shop")
            owner = await self.users.get(data.user_id)
            if owner is None or owner.role != UserRole.VENDOR:
                raise BadRequestError("user_id must reference an existing vendor user")
            owner_id, status = owner.id, ApprovalStatus.APPROVED
        else:
            if data.user_id not in (None, user.id):
                raise PermissionDeniedError("Vendors can only create their own shop")
            owner_id, status = user.id, ApprovalStatus.PENDING

        if await self.repo.get_by_user_id(owner_id):
            raise ConflictError("This vendor already has a shop", code="VENDOR_EXISTS")

        vendor = Vendor(user_id=owner_id, status=status, is_open=False, **data.model_dump(exclude={"user_id"}))
        self.repo.add(vendor)
        if status == ApprovalStatus.PENDING:
            await self.notifications.notify_role(
                UserRole.ADMIN, "Vendor awaiting approval", f"{vendor.shop_name} needs review.", type_="vendor_pending"
            )
        await self.commit()
        return vendor

    async def list_vendors(
        self,
        viewer: User | None,
        *,
        q: str | None,
        status: ApprovalStatus | None,
        is_open: bool | None,
        latitude: float | None,
        longitude: float | None,
        radius_km: float | None,
        limit: int,
        offset: int,
    ) -> tuple[list[VendorOut], int]:
        if (latitude is None) != (longitude is None):
            raise BadRequestError("latitude and longitude must be provided together")

        stmt = select(Vendor)
        if viewer is not None and viewer.role == UserRole.ADMIN:
            if status:
                stmt = stmt.where(Vendor.status == status)
        else:
            stmt = stmt.where(Vendor.status == ApprovalStatus.APPROVED)
        if is_open is not None:
            stmt = stmt.where(Vendor.is_open.is_(is_open))
        if q:
            pattern = f"%{escape_like(q)}%"
            stmt = stmt.where(
                or_(Vendor.shop_name.ilike(pattern, escape="\\"), Vendor.description.ilike(pattern, escape="\\"))
            )

        if latitude is None:
            vendors, total = await self.repo.paginate(stmt.order_by(Vendor.created_at.desc()), limit, offset)
            return [VendorOut.model_validate(v) for v in vendors], total

        # Nearby search: bounding-box pre-filter in SQL, exact haversine distance in Python.
        radius = min(radius_km or get_settings().DEFAULT_SEARCH_RADIUS_KM, get_settings().MAX_SEARCH_RADIUS_KM)
        min_lat, max_lat, min_lon, max_lon = bounding_box(latitude, longitude, radius)
        stmt = stmt.where(Vendor.latitude.between(min_lat, max_lat), Vendor.longitude.between(min_lon, max_lon))
        nearby: list[VendorOut] = []
        for vendor in await self.repo.all(stmt):
            distance = haversine_km(latitude, longitude, vendor.latitude, vendor.longitude)
            if distance <= radius:
                nearby.append(VendorOut.model_validate(vendor).model_copy(update={"distance_km": round(distance, 3)}))
        nearby.sort(key=lambda v: v.distance_km or 0)
        return nearby[offset : offset + limit], len(nearby)

    async def get(self, vendor_id: uuid.UUID, viewer: User | None) -> Vendor:
        vendor = await self._get_or_404(vendor_id)
        if vendor.status != ApprovalStatus.APPROVED and not self._is_owner_or_admin(vendor, viewer):
            raise NotFoundError("Vendor not found")
        return vendor

    async def get_own(self, user: User) -> Vendor:
        vendor = await self.repo.get_by_user_id(user.id)
        if vendor is None:
            raise NotFoundError("You have not created a shop yet", code="VENDOR_PROFILE_NOT_FOUND")
        return vendor

    async def update(self, vendor_id: uuid.UUID, user: User, data: VendorUpdate) -> Vendor:
        vendor = await self._get_managed(vendor_id, user)
        for field, value in data.model_dump(exclude_unset=True, exclude_none=True).items():
            setattr(vendor, field, value)
        await self.commit()
        return vendor

    async def delete(self, vendor_id: uuid.UUID, user: User) -> None:
        vendor = await self._get_managed(vendor_id, user)
        if await self.repo.has_orders(vendor.id):
            raise ConflictError(
                "Shops with order history cannot be deleted; suspend or close the shop instead",
                code="VENDOR_HAS_ORDERS",
            )
        await self.repo.delete(vendor)
        await self.commit()

    async def set_status(self, vendor_id: uuid.UUID, data: VendorStatusUpdate, admin: User) -> Vendor:
        vendor = await self._get_or_404(vendor_id)
        vendor.status = data.status
        if data.status != ApprovalStatus.APPROVED:
            vendor.is_open = False
        title, template = VENDOR_STATUS_MESSAGES[data.status]
        reason = f" Reason: {data.reason}" if data.reason else ""
        self.notifications.notify(
            vendor.user_id, title, template.format(shop=vendor.shop_name, reason=reason), type_="vendor_status"
        )
        await self.commit()
        return vendor

    async def set_availability(self, vendor_id: uuid.UUID, is_open: bool, user: User) -> Vendor:
        vendor = await self._get_managed(vendor_id, user)
        if is_open and vendor.status != ApprovalStatus.APPROVED:
            raise ConflictError("Only approved shops can open for orders", code="VENDOR_NOT_APPROVED")
        vendor.is_open = is_open
        await self.commit()
        return vendor
