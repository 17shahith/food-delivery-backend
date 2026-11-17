import uuid
from datetime import datetime, time, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.enums import ApprovalStatus, OrderStatus, TRACKABLE_ORDER_STATUSES, UserRole
from app.models.order import Order, StatusTimeline
from app.models.rider import Rider
from app.models.user import User
from app.models.vendor import Vendor
from app.repositories.tracking_repository import TrackingRepository
from app.repositories.user_repository import UserRepository
from app.schemas.admin import AdminUserUpdate, LiveDelivery, PlatformAnalytics, TopVendor
from app.schemas.rider import RiderSummary
from app.schemas.tracking import TrackingPoint
from app.services.base import BaseService
from app.utils.exceptions import BadRequestError, ConflictError, NotFoundError
from app.utils.helpers import ensure_aware, money, utcnow


def _counts(rows) -> dict[str, int]:
    return {getattr(key, "value", str(key)): int(count) for key, count in rows}


class AdminService(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.users = UserRepository(session)
        self.tracking = TrackingRepository(session)

    async def list_users(self, *, role: UserRole | None, is_active: bool | None, q: str | None, limit: int, offset: int):
        return await self.users.paginate(self.users.search(role=role, is_active=is_active, q=q), limit, offset)

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found")
        return user

    async def update_user(self, user_id: uuid.UUID, data: AdminUserUpdate, admin: User) -> User:
        user = await self.get_user(user_id)
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        if user.id == admin.id and (changes.get("is_active") is False or changes.get("role", UserRole.ADMIN) != UserRole.ADMIN):
            raise BadRequestError("Administrators cannot deactivate or demote themselves")
        if "role" in changes and changes["role"] != user.role:
            vendor = await self.session.scalar(select(Vendor.id).where(Vendor.user_id == user.id))
            rider = await self.session.scalar(select(Rider.id).where(Rider.user_id == user.id))
            if vendor or rider:
                raise ConflictError("Users with a shop or rider profile cannot change role", code="ROLE_LOCKED")
        for field, value in changes.items():
            setattr(user, field, value)
        await self.commit()
        return user

    async def live_deliveries(self) -> list[LiveDelivery]:
        stmt = (
            select(Order)
            .where(Order.order_status.in_(TRACKABLE_ORDER_STATUSES))
            .options(selectinload(Order.vendor), selectinload(Order.rider).selectinload(Rider.user))
            .order_by(Order.updated_at.desc())
        )
        orders = list((await self.session.execute(stmt)).scalars().all())
        latest = await self.tracking.latest_for_orders([o.id for o in orders])
        return [
            LiveDelivery(
                order_id=o.id,
                order_number=o.order_number,
                order_status=o.order_status,
                vendor_name=o.vendor.shop_name,
                delivery_address=o.delivery_address,
                rider=RiderSummary.model_validate(o.rider) if o.rider else None,
                current_location=TrackingPoint.model_validate(latest[o.id]) if o.id in latest else None,
                updated_at=o.updated_at,
            )
            for o in orders
        ]

    async def analytics(self) -> PlatformAnalytics:
        s = self.session
        now = utcnow()
        start_of_day = datetime.combine(now.date(), time.min, tzinfo=timezone.utc)

        users_by_role = _counts((await s.execute(select(User.role, func.count()).group_by(User.role))).all())
        vendors_by_status = _counts((await s.execute(select(Vendor.status, func.count()).group_by(Vendor.status))).all())
        riders_by_availability = _counts(
            (await s.execute(
                select(Rider.availability, func.count()).where(Rider.status == ApprovalStatus.APPROVED).group_by(Rider.availability)
            )).all()
        )
        orders_by_status = _counts((await s.execute(select(Order.order_status, func.count()).group_by(Order.order_status))).all())
        total_orders = sum(orders_by_status.values())
        orders_today = int(await s.scalar(select(func.count()).where(Order.created_at >= start_of_day)) or 0)
        active_deliveries = sum(orders_by_status.get(st.value, 0) for st in TRACKABLE_ORDER_STATUSES)

        delivered = Order.order_status == OrderStatus.DELIVERED
        revenue = Decimal(str(await s.scalar(select(func.coalesce(func.sum(Order.total_amount), 0)).where(delivered)) or 0))
        delivered_count = orders_by_status.get(OrderStatus.DELIVERED.value, 0)
        avg_value = money(revenue / delivered_count) if delivered_count else money(0)

        # Average minutes from placement to DELIVERED timeline entry (latest 500 deliveries; portable SQL).
        rows = (
            await s.execute(
                select(Order.created_at, StatusTimeline.created_at)
                .join(StatusTimeline, StatusTimeline.order_id == Order.id)
                .where(StatusTimeline.status == OrderStatus.DELIVERED)
                .order_by(StatusTimeline.created_at.desc())
                .limit(500)
            )
        ).all()
        durations = [(ensure_aware(done) - ensure_aware(placed)).total_seconds() / 60 for placed, done in rows]
        avg_minutes = round(sum(durations) / len(durations), 1) if durations else None

        top_rows = (
            await s.execute(
                select(Vendor.id, Vendor.shop_name, func.count(Order.id), func.sum(Order.total_amount))
                .join(Order, Order.vendor_id == Vendor.id)
                .where(delivered)
                .group_by(Vendor.id, Vendor.shop_name)
                .order_by(func.sum(Order.total_amount).desc())
                .limit(5)
            )
        ).all()
        top_vendors = [
            TopVendor(vendor_id=vid, shop_name=name, delivered_orders=int(cnt), revenue=money(total or 0))
            for vid, name, cnt, total in top_rows
        ]

        return PlatformAnalytics(
            users_by_role=users_by_role,
            vendors_by_status=vendors_by_status,
            riders_by_availability=riders_by_availability,
            orders_by_status=orders_by_status,
            total_orders=total_orders,
            orders_today=orders_today,
            active_deliveries=active_deliveries,
            delivered_revenue=money(revenue),
            average_order_value=avg_value,
            average_delivery_minutes=avg_minutes,
            top_vendors=top_vendors,
            generated_at=now,
        )
