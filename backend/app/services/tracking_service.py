"""GPS tracking with write throttling.

Every accepted point is broadcast over Supabase Realtime immediately, but it is only written to
Postgres (rider position + `delivery_coordinates` history) when the rider has moved at least
GPS_MIN_DISTANCE_METERS and GPS_MIN_INTERVAL_SECONDS have passed, or when GPS_HEARTBEAT_SECONDS
have elapsed since the last stored point. A rider sending a fix every second while stopped at a
traffic light therefore produces one row per heartbeat instead of one per second.
"""

import uuid
from datetime import datetime

from app.core.config import get_settings
from app.models.enums import TRACKABLE_ORDER_STATUSES, ApprovalStatus, UserRole
from app.models.order import Order
from app.models.rider import Rider
from app.models.tracking import DeliveryCoordinate, RiderLocation
from app.models.user import User
from app.repositories.order_repository import OrderRepository
from app.repositories.rider_repository import RiderRepository
from app.repositories.tracking_repository import TrackingRepository
from app.schemas.rider import RiderSummary
from app.schemas.tracking import (
    GeoPoint,
    IdleLocationUpdate,
    LocationUpdate,
    LocationUpdateResponse,
    TrackingHistoryResponse,
    TrackingPoint,
    TrackingResponse,
)
from app.services.base import BaseService
from app.services.order_workflow import can_view_order
from app.services.realtime_service import ADMIN_TOPIC, order_topic
from app.utils.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.utils.helpers import ensure_aware, haversine_km, utcnow


def should_persist(rider: Rider, latitude: float, longitude: float, now: datetime) -> bool:
    settings = get_settings()
    if rider.last_location_at is None or rider.latitude is None or rider.longitude is None:
        return True
    elapsed = (now - ensure_aware(rider.last_location_at)).total_seconds()
    if elapsed >= settings.GPS_HEARTBEAT_SECONDS:
        return True
    if elapsed < settings.GPS_MIN_INTERVAL_SECONDS:
        return False
    moved_m = haversine_km(rider.latitude, rider.longitude, latitude, longitude) * 1000
    return moved_m >= settings.GPS_MIN_DISTANCE_METERS


class TrackingService(BaseService):
    def __init__(self, session, publisher=None):
        super().__init__(session, publisher)
        self.repo = TrackingRepository(session)
        self.orders = OrderRepository(session)
        self.riders = RiderRepository(session)

    async def update_location(self, user: User, data: LocationUpdate | IdleLocationUpdate) -> LocationUpdateResponse:
        """Record a GPS fix. With an `order_id` the rider must be assigned to that live delivery;
        without one (rider online, between jobs) only the rider's own position is updated."""
        rider = await self.riders.get_by_user_id(user.id)
        if rider is None:
            raise NotFoundError("Rider profile not found", code="RIDER_PROFILE_NOT_FOUND")
        if rider.status != ApprovalStatus.APPROVED:
            raise PermissionDeniedError("Your rider account is not approved", code="RIDER_NOT_APPROVED")

        order: Order | None = None
        if data.order_id is not None:
            order = await self.orders.get(data.order_id)
            if order is None or order.rider_id != rider.id:
                raise PermissionDeniedError("You are not assigned to this order", code="NOT_ASSIGNED")
            if order.order_status not in TRACKABLE_ORDER_STATUSES:
                raise ConflictError(
                    f"Tracking is not active for orders in status {order.order_status.value}",
                    code="TRACKING_NOT_ACTIVE",
                )

        now = utcnow()
        latest = await self.session.get(RiderLocation, rider.id)
        # The first fix of a new delivery (or after one ends) is always stored so viewers get a position at once.
        context_changed = latest is None or latest.order_id != (order.id if order else None)
        persisted = context_changed or should_persist(rider, data.latitude, data.longitude, now)
        if persisted:
            rider.latitude, rider.longitude, rider.last_location_at = data.latitude, data.longitude, now
            self._upsert_latest(latest, rider, order, data, now)
            if order is not None:
                self.repo.add(
                    DeliveryCoordinate(
                        order_id=order.id,
                        rider_id=rider.id,
                        latitude=data.latitude,
                        longitude=data.longitude,
                        heading=data.heading,
                        speed=data.speed,
                        accuracy=data.accuracy,
                        recorded_at=now,
                    )
                )

        payload = {
            "order_id": order.id if order else None,
            "rider_id": rider.id,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "heading": data.heading,
            "speed": data.speed,
            "accuracy": data.accuracy,
            "recorded_at": now,
            "order_status": order.order_status.value if order else None,
        }
        if order is not None:
            self.queue_event(order_topic(order.id), "location_updated", payload)
        self.queue_event(ADMIN_TOPIC, "location_updated", payload)
        await self.commit()  # no-op transaction when the point was throttled; still publishes events
        return LocationUpdateResponse(
            order_id=order.id if order else None,
            latitude=data.latitude,
            longitude=data.longitude,
            recorded_at=now,
            persisted=persisted,
        )

    def _upsert_latest(
        self, latest: RiderLocation | None, rider: Rider, order: Order | None, data, now: datetime
    ) -> None:
        """One row per rider in `rider_locations` (streamed to the web app via Postgres Changes)."""
        if latest is None:
            latest = RiderLocation(rider_id=rider.id)
            self.session.add(latest)
        latest.order_id = order.id if order else None
        latest.latitude, latest.longitude = data.latitude, data.longitude
        latest.heading, latest.speed, latest.accuracy = data.heading, data.speed, data.accuracy
        latest.updated_at = now

    async def _get_trackable_order(self, order_id: uuid.UUID, user: User) -> Order:
        order = await self.orders.get_detail(order_id)
        if order is None:
            raise NotFoundError("Order not found")
        if not can_view_order(order, user):
            raise PermissionDeniedError("You are not allowed to track this order", code="TRACKING_FORBIDDEN")
        return order

    async def get_current(self, order_id: uuid.UUID, user: User) -> TrackingResponse:
        order = await self._get_trackable_order(order_id, user)
        is_live = order.order_status in TRACKABLE_ORDER_STATUSES
        # Customers may only see the rider's position while the delivery is in progress.
        if user.role == UserRole.CUSTOMER and not is_live:
            raise ConflictError("Live tracking is not available for this order right now", code="TRACKING_NOT_ACTIVE")
        latest = await self.repo.latest_for_order(order.id)
        return TrackingResponse(
            order_id=order.id,
            order_number=order.order_number,
            order_status=order.order_status,
            is_live=is_live,
            rider=RiderSummary.model_validate(order.rider) if order.rider else None,
            current_location=TrackingPoint.model_validate(latest) if latest else None,
            pickup=GeoPoint(address=order.vendor.address, latitude=order.vendor.latitude, longitude=order.vendor.longitude),
            destination=GeoPoint(
                address=order.delivery_address, latitude=order.delivery_latitude, longitude=order.delivery_longitude
            ),
            realtime_channel=order_topic(order.id),
        )

    async def get_history(self, order_id: uuid.UUID, user: User, limit: int) -> TrackingHistoryResponse:
        order = await self._get_trackable_order(order_id, user)
        points, total = await self.repo.history(order.id, limit)
        return TrackingHistoryResponse(
            order_id=order.id, points=[TrackingPoint.model_validate(p) for p in points], total=total
        )
