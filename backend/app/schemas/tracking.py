import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import OrderStatus
from app.schemas.common import Latitude, Longitude, ORMModel
from app.schemas.rider import RiderSummary


class IdleLocationUpdate(BaseModel):
    """GPS fix; `order_id` is optional (omitted while the rider is online between deliveries)."""

    order_id: uuid.UUID | None = None
    latitude: Latitude
    longitude: Longitude
    heading: float | None = Field(None, ge=0, lt=360, description="Degrees clockwise from north")
    speed: float | None = Field(None, ge=0, le=100, description="Metres per second")
    accuracy: float | None = Field(None, ge=0, le=10000, description="Horizontal accuracy in metres")


class LocationUpdate(IdleLocationUpdate):
    order_id: uuid.UUID


class LocationUpdateResponse(BaseModel):
    order_id: uuid.UUID | None
    latitude: float
    longitude: float
    recorded_at: datetime
    persisted: bool = Field(description="False when the point was throttled (broadcast only, not stored)")


class TrackingPoint(ORMModel):
    latitude: float
    longitude: float
    heading: float | None = None
    speed: float | None = None
    accuracy: float | None = None
    recorded_at: datetime


class GeoPoint(BaseModel):
    address: str | None
    latitude: float | None
    longitude: float | None


class TrackingResponse(BaseModel):
    order_id: uuid.UUID
    order_number: str
    order_status: OrderStatus
    is_live: bool
    rider: RiderSummary | None
    current_location: TrackingPoint | None
    pickup: GeoPoint
    destination: GeoPoint
    realtime_channel: str = Field(description="Supabase Realtime broadcast topic for live updates")


class TrackingHistoryResponse(BaseModel):
    order_id: uuid.UUID
    points: list[TrackingPoint]
    total: int
