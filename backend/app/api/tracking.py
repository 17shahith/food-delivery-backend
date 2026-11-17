import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, RiderUser, get_tracking_service
from app.schemas.common import AUTH_ERRORS, error_responses
from app.schemas.tracking import LocationUpdate, LocationUpdateResponse, TrackingHistoryResponse, TrackingResponse
from app.services.tracking_service import TrackingService

router = APIRouter(prefix="/tracking", tags=["Tracking"])
Service = Annotated[TrackingService, Depends(get_tracking_service)]


@router.post(
    "/location",
    response_model=LocationUpdateResponse,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Report the rider's GPS position",
    description="Only the rider assigned to the order may report, and only while the order is RIDER_ASSIGNED, "
    "PICKED_UP or OUT_FOR_DELIVERY. Every point is broadcast on Realtime topic `order:{order_id}`; "
    "database writes are throttled (`persisted=false` for skipped points).",
)
async def update_location(payload: LocationUpdate, user: RiderUser, service: Service) -> LocationUpdateResponse:
    return await service.update_location(user, payload)


@router.get(
    "/{order_id}",
    response_model=TrackingResponse,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Current rider location for an order",
    description="Available to the order's customer (only while the delivery is live), vendor, rider and admins.",
)
async def get_tracking(order_id: uuid.UUID, user: CurrentUser, service: Service) -> TrackingResponse:
    return await service.get_current(order_id, user)


@router.get(
    "/{order_id}/history",
    response_model=TrackingHistoryResponse,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Stored route breadcrumbs for an order (oldest first)",
)
async def get_tracking_history(
    order_id: uuid.UUID,
    user: CurrentUser,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> TrackingHistoryResponse:
    return await service.get_history(order_id, user, limit)
