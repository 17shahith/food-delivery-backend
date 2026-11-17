import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ApprovalStatus, AssignmentStatus, RiderAvailability, VehicleType
from app.schemas.common import Latitude, Longitude, ORMModel


class RiderCreate(BaseModel):
    user_id: uuid.UUID | None = Field(None, description="Admins only: create a profile for an existing rider user")
    vehicle_type: VehicleType
    vehicle_number: str | None = Field(None, max_length=30)


class RiderUpdate(BaseModel):
    vehicle_type: VehicleType | None = None
    vehicle_number: str | None = Field(None, max_length=30)


class RiderAvailabilityUpdate(BaseModel):
    availability: Literal[RiderAvailability.ONLINE, RiderAvailability.OFFLINE]
    latitude: Latitude | None = None
    longitude: Longitude | None = None

    @model_validator(mode="after")
    def _both_coordinates(self) -> "RiderAvailabilityUpdate":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class RiderStatusUpdate(BaseModel):
    status: ApprovalStatus
    reason: str | None = Field(None, max_length=500)


class RiderOut(ORMModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    phone: str | None
    vehicle_type: VehicleType
    vehicle_number: str | None
    status: ApprovalStatus
    availability: RiderAvailability
    latitude: float | None
    longitude: float | None
    last_location_at: datetime | None
    created_at: datetime
    distance_km: float | None = None


class RiderSummary(ORMModel):
    id: uuid.UUID
    name: str
    phone: str | None
    vehicle_type: VehicleType
    vehicle_number: str | None


class AssignRiderRequest(BaseModel):
    order_id: uuid.UUID
    rider_id: uuid.UUID


class AssignmentRejectRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


class AssignmentOut(ORMModel):
    id: uuid.UUID
    order_id: uuid.UUID
    rider_id: uuid.UUID
    assigned_by: uuid.UUID | None
    status: AssignmentStatus
    rejection_reason: str | None
    created_at: datetime
    responded_at: datetime | None
