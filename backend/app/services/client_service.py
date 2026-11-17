"""Queries and operations used only by the web-app API (`/api/app`).

Order workflow, assignment, tracking and auth logic is reused from the domain services; this
module adds the address book, dashboards (stats/analytics) and the app's list filters.
"""

import uuid
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import selectinload

from app.models.address import CustomerAddress
from app.models.enums import (
    ACTIVE_ORDER_STATUSES,
    ApprovalStatus,
    OrderStatus,
    RiderAvailability,
    UserRole,
)
from app.models.notification import Notification
from app.models.order import Order, StatusTimeline
from app.models.product import Product
from app.models.rider import Rider
from app.models.tracking import DeliveryCoordinate, RiderLocation
from app.models.user import User
from app.models.vendor import Vendor
from app.repositories.order_repository import ORDER_DETAIL_OPTIONS
from app.schemas.client import AddressIn, AddressPatch, AdminStatsOut, AnalyticsOut, RiderStatsOut, VendorStatsOut
from app.services.base import BaseService
from app.services.client_mappers import app_status
from app.utils.exceptions import NotFoundError
from app.utils.helpers import ensure_aware, escape_like, haversine_km, utcnow

LIST_LIMIT = 200
VENDOR_ACTIVE = (OrderStatus.VENDOR_ACCEPTED, OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP)
NOT_FULFILLED = (OrderStatus.REJECTED, OrderStatus.CANCELLED)


def _start_of_today() -> datetime:
    return datetime.combine(utcnow().date(), time.min, tzinfo=timezone.utc)


def _like(term: str) -> str:
    return f"%{escape_like(term)}%"


class ClientService(BaseService):
    # ------------------------------------------------------------------ users
    async def approval_for(self, user: User) -> ApprovalStatus | None:
        if user.role == UserRole.VENDOR:
            status = await self.session.scalar(select(Vendor.status).where(Vendor.user_id == user.id))
            return status or ApprovalStatus.PENDING
        if user.role == UserRole.RIDER:
            status = await self.session.scalar(select(Rider.status).where(Rider.user_id == user.id))
            return status or ApprovalStatus.PENDING
        return None

    async def approvals_for(self, users: list[User]) -> dict[uuid.UUID, ApprovalStatus | None]:
        ids = [u.id for u in users]
        vendor_rows = (await self.session.execute(select(Vendor.user_id, Vendor.status).where(Vendor.user_id.in_(ids)))).all()
        rider_rows = (await self.session.execute(select(Rider.user_id, Rider.status).where(Rider.user_id.in_(ids)))).all()
        known = dict(vendor_rows) | dict(rider_rows)
        result: dict[uuid.UUID, ApprovalStatus | None] = {}
        for u in users:
            if u.role in (UserRole.VENDOR, UserRole.RIDER):
                result[u.id] = known.get(u.id, ApprovalStatus.PENDING)
            else:
                result[u.id] = None
        return result

    # ------------------------------------------------------------------ addresses
    async def list_addresses(self, user: User) -> list[CustomerAddress]:
        stmt = (
            select(CustomerAddress)
            .where(CustomerAddress.user_id == user.id)
            .order_by(CustomerAddress.is_default.desc(), CustomerAddress.created_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_address(self, user: User, address_id: uuid.UUID) -> CustomerAddress:
        address = await self.session.get(CustomerAddress, address_id)
        if address is None or address.user_id != user.id:
            raise NotFoundError("Address not found")
        return address

    async def _clear_default(self, user: User) -> None:
        await self.session.execute(
            update(CustomerAddress).where(CustomerAddress.user_id == user.id).values(is_default=False)
        )

    async def create_address(self, user: User, data: AddressIn) -> CustomerAddress:
        has_any = await self.session.scalar(select(func.count()).where(CustomerAddress.user_id == user.id))
        make_default = data.is_default or not has_any
        if make_default:
            await self._clear_default(user)
        address = CustomerAddress(user_id=user.id, **data.model_dump(exclude={"is_default"}), is_default=make_default)
        self.session.add(address)
        await self.commit()
        return address

    async def update_address(self, user: User, address_id: uuid.UUID, data: AddressPatch) -> CustomerAddress:
        address = await self.get_address(user, address_id)
        changes = data.model_dump(exclude_unset=True)
        if changes.get("is_default"):
            await self._clear_default(user)
            await self.session.refresh(address)
        for field, value in changes.items():
            if value is None and field not in {"line2", "state"}:
                continue
            setattr(address, field, value)
        await self.commit()
        return address

    async def delete_address(self, user: User, address_id: uuid.UUID) -> None:
        address = await self.get_address(user, address_id)
        was_default = address.is_default
        await self.session.delete(address)
        await self.session.flush()
        if was_default:
            replacement = (
                await self.session.execute(
                    select(CustomerAddress).where(CustomerAddress.user_id == user.id)
                    .order_by(CustomerAddress.created_at.desc()).limit(1)
                )
            ).scalars().first()
            if replacement is not None:
                replacement.is_default = True
        await self.commit()

    # ------------------------------------------------------------------ vendors & products
    async def list_vendors(
        self,
        *,
        latitude: float | None,
        longitude: float | None,
        radius_km: float | None,
        search: str | None,
        category: str | None,
        open_now: bool | None,
        sort: str | None,
    ) -> list[tuple[Vendor, float | None]]:
        stmt = select(Vendor).where(Vendor.status == ApprovalStatus.APPROVED)
        if search:
            stmt = stmt.where(or_(Vendor.shop_name.ilike(_like(search), escape="\\"),
                                  Vendor.category.ilike(_like(search), escape="\\"),
                                  Vendor.description.ilike(_like(search), escape="\\")))
        if category:
            stmt = stmt.where(Vendor.category == category)
        if open_now:
            stmt = stmt.where(Vendor.is_open.is_(True))
        vendors = list((await self.session.execute(stmt.limit(1000))).scalars().all())

        here = latitude is not None and longitude is not None
        results = []
        for vendor in vendors:
            distance = round(haversine_km(latitude, longitude, vendor.latitude, vendor.longitude), 2) if here else None
            if radius_km is not None and distance is not None and distance > radius_km:
                continue
            results.append((vendor, distance))
        if sort == "name" or (sort in (None, "distance") and not here) or sort == "rating":
            results.sort(key=lambda pair: pair[0].shop_name.lower())
        else:
            results.sort(key=lambda pair: pair[1] or 0)
        return results[:LIST_LIMIT]

    async def own_vendor(self, user: User) -> Vendor:
        vendor = await self.session.scalar(select(Vendor).where(Vendor.user_id == user.id))
        if vendor is None:
            raise NotFoundError("Store not found", code="VENDOR_PROFILE_NOT_FOUND")
        return vendor

    async def vendor_stats(self, vendor: Vendor) -> VendorStatsOut:
        today = _start_of_today()
        by_status = dict(
            (await self.session.execute(
                select(Order.order_status, func.count()).where(Order.vendor_id == vendor.id).group_by(Order.order_status)
            )).all()
        )
        orders_today = await self.session.scalar(
            select(func.count()).where(Order.vendor_id == vendor.id, Order.created_at >= today)
        )
        revenue_today = await self.session.scalar(
            select(func.coalesce(func.sum(Order.subtotal), 0)).where(
                Order.vendor_id == vendor.id, Order.created_at >= today, Order.order_status.not_in(NOT_FULFILLED)
            )
        )
        products = await self.session.scalar(select(func.count()).where(Product.vendor_id == vendor.id))
        return VendorStatsOut(
            orders_today=int(orders_today or 0),
            revenue_today=float(revenue_today or 0),
            pending_orders=int(by_status.get(OrderStatus.ORDER_PLACED, 0)),
            active_orders=sum(int(by_status.get(s, 0)) for s in VENDOR_ACTIVE),
            total_products=int(products or 0),
            completed_orders=int(by_status.get(OrderStatus.DELIVERED, 0)),
        )

    async def list_products(
        self,
        *,
        vendor_id: uuid.UUID | None = None,
        own_vendor_id: uuid.UUID | None = None,
        search: str | None = None,
        category: str | None = None,
        min_price: float | None = None,
        max_price: float | None = None,
        available: bool | None = None,
    ) -> list[Product]:
        stmt = select(Product).join(Product.vendor).options(selectinload(Product.vendor))
        if own_vendor_id is not None:
            stmt = stmt.where(Product.vendor_id == own_vendor_id)
        else:
            stmt = stmt.where(Vendor.status == ApprovalStatus.APPROVED)
            if vendor_id is not None:
                stmt = stmt.where(Product.vendor_id == vendor_id)
        if search:
            stmt = stmt.where(or_(Product.name.ilike(_like(search), escape="\\"),
                                  Product.description.ilike(_like(search), escape="\\"),
                                  Product.category.ilike(_like(search), escape="\\")))
        if category:
            stmt = stmt.where(Product.category == category)
        if min_price is not None:
            stmt = stmt.where(Product.price >= Decimal(str(min_price)))
        if max_price is not None:
            stmt = stmt.where(Product.price <= Decimal(str(max_price)))
        if available is not None:
            stmt = stmt.where(Product.availability.is_(available))
        stmt = stmt.order_by(Product.name).limit(LIST_LIMIT if own_vendor_id is None else 1000)
        return list((await self.session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------ orders
    async def list_orders(
        self,
        *,
        customer_id: uuid.UUID | None = None,
        vendor_id: uuid.UUID | None = None,
        rider_id: uuid.UUID | None = None,
        statuses: list[OrderStatus] | None = None,
        search: str | None = None,
    ) -> list[Order]:
        stmt = select(Order).options(*ORDER_DETAIL_OPTIONS)
        if customer_id is not None:
            stmt = stmt.where(Order.customer_id == customer_id)
        if vendor_id is not None:
            stmt = stmt.where(Order.vendor_id == vendor_id)
        if rider_id is not None:
            stmt = stmt.where(Order.rider_id == rider_id)
        if statuses:
            stmt = stmt.where(Order.order_status.in_(statuses))
        if search:
            pattern = _like(search)
            stmt = (
                stmt.join(Vendor, Vendor.id == Order.vendor_id)
                .join(User, User.id == Order.customer_id)
                .where(or_(Order.order_number.ilike(pattern, escape="\\"),
                           Vendor.shop_name.ilike(pattern, escape="\\"),
                           User.name.ilike(pattern, escape="\\")))
            )
        stmt = stmt.order_by(Order.created_at.desc()).limit(LIST_LIMIT)
        return list((await self.session.execute(stmt)).scalars().unique().all())

    async def load_order(self, order_id: uuid.UUID) -> Order:
        order = (
            await self.session.execute(
                select(Order).where(Order.id == order_id).options(*ORDER_DETAIL_OPTIONS).execution_options(populate_existing=True)
            )
        ).scalars().first()
        if order is None:
            raise NotFoundError("Order not found")
        return order

    # ------------------------------------------------------------------ riders
    async def own_rider(self, user: User) -> Rider:
        rider = await self.session.scalar(
            select(Rider).where(Rider.user_id == user.id).options(selectinload(Rider.user))
        )
        if rider is None:
            raise NotFoundError("Rider profile not found", code="RIDER_PROFILE_NOT_FOUND")
        return rider

    async def delivered_counts(self, rider_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        if not rider_ids:
            return {}
        rows = await self.session.execute(
            select(Order.rider_id, func.count())
            .where(Order.rider_id.in_(rider_ids), Order.order_status == OrderStatus.DELIVERED)
            .group_by(Order.rider_id)
        )
        return {rid: int(n) for rid, n in rows.all()}

    async def rider_stats(self, rider: Rider) -> RiderStatsOut:
        delivered = Order.order_status == OrderStatus.DELIVERED
        today = _start_of_today()
        total = await self.session.scalar(select(func.count()).where(Order.rider_id == rider.id, delivered))
        row = (
            await self.session.execute(
                select(func.count(), func.coalesce(func.sum(Order.rider_earning), 0)).where(
                    Order.rider_id == rider.id, delivered, Order.updated_at >= today
                )
            )
        ).one()
        return RiderStatsOut(
            deliveries_today=int(row[0] or 0), earnings_today=float(row[1] or 0), total_deliveries=int(total or 0)
        )

    # ------------------------------------------------------------------ tracking
    async def latest_order_location(self, order: Order) -> DeliveryCoordinate | None:
        stmt = (
            select(DeliveryCoordinate)
            .where(DeliveryCoordinate.order_id == order.id)
            .order_by(DeliveryCoordinate.recorded_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def active_locations(self, max_age_minutes: int = 10) -> list[tuple[RiderLocation, str]]:
        cutoff = utcnow() - timedelta(minutes=max_age_minutes)
        stmt = (
            select(RiderLocation, User.name)
            .join(Rider, Rider.id == RiderLocation.rider_id)
            .join(User, User.id == Rider.user_id)
            .where(Rider.availability != RiderAvailability.OFFLINE, RiderLocation.updated_at >= cutoff)
        )
        return [(loc, name) for loc, name in (await self.session.execute(stmt)).all()]

    # ------------------------------------------------------------------ notifications
    async def notifications(self, user: User, limit: int = 100) -> list[Notification]:
        stmt = (
            select(Notification).where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc()).limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def delete_notification(self, user: User, notification_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(Notification).where(Notification.id == notification_id, Notification.user_id == user.id)
        )
        await self.commit()

    # ------------------------------------------------------------------ admin
    async def admin_users(self, *, role: UserRole | None, search: str | None, active: bool | None) -> list[User]:
        stmt = select(User)
        if role:
            stmt = stmt.where(User.role == role)
        if search:
            pattern = _like(search)
            stmt = stmt.where(or_(User.name.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\"),
                                  User.phone.ilike(pattern, escape="\\")))
        if active is not None:
            stmt = stmt.where(User.is_active.is_(active))
        return list((await self.session.execute(stmt.order_by(User.created_at.desc()).limit(500))).scalars().all())

    async def admin_vendors(self, *, search: str | None, status: ApprovalStatus | None) -> list[Vendor]:
        stmt = select(Vendor).join(User, User.id == Vendor.user_id).options(selectinload(Vendor.user))
        if status:
            stmt = stmt.where(Vendor.status == status)
        if search:
            pattern = _like(search)
            stmt = stmt.where(or_(Vendor.shop_name.ilike(pattern, escape="\\"), User.name.ilike(pattern, escape="\\")))
        return list((await self.session.execute(stmt.order_by(Vendor.created_at.desc()).limit(500))).scalars().all())

    async def admin_riders(self, *, search: str | None, status: ApprovalStatus | None) -> list[Rider]:
        stmt = select(Rider).join(User, User.id == Rider.user_id).options(selectinload(Rider.user))
        if status:
            stmt = stmt.where(Rider.status == status)
        if search:
            pattern = _like(search)
            stmt = stmt.where(or_(User.name.ilike(pattern, escape="\\"), User.phone.ilike(pattern, escape="\\"),
                                  Rider.vehicle_number.ilike(pattern, escape="\\")))
        return list((await self.session.execute(stmt.order_by(Rider.created_at.desc()).limit(500))).scalars().all())

    async def admin_stats(self) -> AdminStatsOut:
        s = self.session
        today = _start_of_today()
        count = lambda stmt: s.scalar(stmt)  # noqa: E731
        return AdminStatsOut(
            total_customers=int(await count(select(func.count()).where(User.role == UserRole.CUSTOMER)) or 0),
            total_vendors=int(await count(select(func.count()).select_from(Vendor)) or 0),
            total_riders=int(await count(select(func.count()).select_from(Rider)) or 0),
            active_orders=int(await count(select(func.count()).where(Order.order_status.in_(ACTIVE_ORDER_STATUSES))) or 0),
            orders_today=int(await count(select(func.count()).where(Order.created_at >= today)) or 0),
            revenue_today=float(
                await count(
                    select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                        Order.order_status == OrderStatus.DELIVERED, Order.updated_at >= today
                    )
                ) or 0
            ),
            pending_approvals=int(
                (await count(select(func.count()).where(Vendor.status == ApprovalStatus.PENDING)) or 0)
                + (await count(select(func.count()).where(Rider.status == ApprovalStatus.PENDING)) or 0)
            ),
            online_riders=int(
                await count(
                    select(func.count()).where(
                        Rider.status == ApprovalStatus.APPROVED, Rider.availability != RiderAvailability.OFFLINE
                    )
                ) or 0
            ),
        )

    async def analytics(self, days: int) -> AnalyticsOut:
        start = _start_of_today() - timedelta(days=days - 1)
        orders = (
            await self.session.execute(
                select(Order.id, Order.vendor_id, Order.order_status, Order.total_amount, Order.created_at)
                .where(Order.created_at >= start)
            )
        ).all()
        delivered_at = dict(
            (await self.session.execute(
                select(StatusTimeline.order_id, StatusTimeline.created_at)
                .join(Order, Order.id == StatusTimeline.order_id)
                .where(Order.created_at >= start, StatusTimeline.status == OrderStatus.DELIVERED)
            )).all()
        )

        daily: dict[str, dict[str, float]] = {
            (start + timedelta(days=i)).date().isoformat(): {"orders": 0, "revenue": 0.0} for i in range(days)
        }
        statuses: dict[OrderStatus, int] = defaultdict(int)
        by_vendor: dict[uuid.UUID, dict[str, float]] = defaultdict(lambda: {"orders": 0, "revenue": 0.0})
        durations: list[float] = []
        for order_id, vendor_id, status, total, created_at in orders:
            created = ensure_aware(created_at)
            bucket = daily.get(created.date().isoformat())
            statuses[status] += 1
            if bucket is not None and status not in NOT_FULFILLED:
                bucket["orders"] += 1
            if status == OrderStatus.DELIVERED:
                if bucket is not None:
                    bucket["revenue"] += float(total)
                by_vendor[vendor_id]["orders"] += 1
                by_vendor[vendor_id]["revenue"] += float(total)
                if order_id in delivered_at:
                    minutes = (ensure_aware(delivered_at[order_id]) - created).total_seconds() / 60
                    if minutes > 0:
                        durations.append(minutes)

        top_ids = sorted(by_vendor, key=lambda vid: by_vendor[vid]["revenue"], reverse=True)[:5]
        names = dict(
            (await self.session.execute(select(Vendor.id, Vendor.shop_name).where(Vendor.id.in_(top_ids)))).all()
        ) if top_ids else {}
        return AnalyticsOut(
            daily=[{"date": d, "orders": int(v["orders"]), "revenue": round(v["revenue"], 2)} for d, v in daily.items()],
            status_breakdown=[{"status": app_status(s), "count": n} for s, n in statuses.items()],
            top_vendors=[
                {"vendor_id": vid, "name": names.get(vid, "Unknown"), "orders": int(by_vendor[vid]["orders"]),
                 "revenue": round(by_vendor[vid]["revenue"], 2)}
                for vid in top_ids
            ],
            avg_delivery_minutes=round(sum(durations) / len(durations), 1) if durations else None,
        )
