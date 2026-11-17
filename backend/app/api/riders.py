import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status

from app.api.deps import (
    AdminUser,
    CurrentUser,
    PaginationDep,
    RiderOrAdmin,
    RiderUser,
    VendorOrAdmin,
    get_rider_service,
)
from app.models.enums import ApprovalStatus, AssignmentStatus, RiderAvailability
from app.schemas.common import AUTH_ERRORS, Page, error_responses
from app.schemas.rider import (
    AssignmentOut,
    AssignmentRejectRequest,
    AssignRiderRequest,
    RiderAvailabilityUpdate,
    RiderCreate,
    RiderOut,
    RiderUpdate,
)
from app.services.rider_service import RiderService

router = APIRouter(prefix="/riders", tags=["Riders"])
Service = Annotated[RiderService, Depends(get_rider_service)]

# NOTE: static paths are declared before /{rider_id} so they are not captured by the path parameter.


@router.post(
    "",
    response_model=RiderOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 409, *AUTH_ERRORS),
    summary="Create a rider profile",
    description="Riders create their own profile (status `pending`); admins may create one for a rider user via `user_id`.",
)
async def create_rider(payload: RiderCreate, user: RiderOrAdmin, service: Service) -> RiderOut:
    return RiderOut.model_validate(await service.create(user, payload))


@router.get("", response_model=Page[RiderOut], responses=error_responses(*AUTH_ERRORS), summary="List riders (admin)")
async def list_riders(
    _: AdminUser,
    service: Service,
    page: PaginationDep,
    status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    availability: RiderAvailability | None = None,
) -> Page[RiderOut]:
    items, total = await service.list_riders(status=status_, availability=availability, limit=page.limit, offset=page.offset)
    return Page(items=[RiderOut.model_validate(r) for r in items], total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/available",
    response_model=list[RiderOut],
    responses=error_responses(400, 404, *AUTH_ERRORS),
    summary="Find nearby available riders",
    description="Approved, online riders with a recent GPS fix, sorted by distance. Search around "
    "`latitude`/`longitude`, or pass `order_id` to search around that order's shop.",
)
async def available_riders(
    user: VendorOrAdmin,
    service: Service,
    latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    order_id: uuid.UUID | None = None,
    radius_km: Annotated[float | None, Query(gt=0, le=50)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[RiderOut]:
    return await service.find_available(
        user, latitude=latitude, longitude=longitude, order_id=order_id, radius_km=radius_km, limit=limit
    )


@router.get("/me", response_model=RiderOut, responses=error_responses(404, *AUTH_ERRORS), summary="Own rider profile")
async def my_rider_profile(user: RiderUser, service: Service) -> RiderOut:
    return RiderOut.model_validate(await service.get_own(user))


@router.put(
    "/availability",
    response_model=RiderOut,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Go online / offline",
    description="Only approved riders can go online. Availability cannot change during an active delivery.",
)
async def set_availability(payload: RiderAvailabilityUpdate, user: RiderUser, service: Service) -> RiderOut:
    return RiderOut.model_validate(await service.set_availability(user, payload))


@router.post(
    "/assign",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Offer a READY_FOR_PICKUP order to a rider",
    description="Creates a pending assignment the rider must accept. Conflicts (order already offered or "
    "assigned, rider busy or with another pending offer) return 409.",
)
async def assign_rider(payload: AssignRiderRequest, user: VendorOrAdmin, service: Service) -> AssignmentOut:
    return AssignmentOut.model_validate(await service.assign(user, payload.order_id, payload.rider_id))


@router.get(
    "/assignments",
    response_model=Page[AssignmentOut],
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Own delivery assignments",
)
async def my_assignments(
    user: RiderUser,
    service: Service,
    page: PaginationDep,
    status_: Annotated[AssignmentStatus | None, Query(alias="status")] = None,
) -> Page[AssignmentOut]:
    items, total = await service.list_assignments(user, status_, page.limit, page.offset)
    return Page(items=[AssignmentOut.model_validate(a) for a in items], total=total, limit=page.limit, offset=page.offset)


@router.post(
    "/assignments/{assignment_id}/accept",
    response_model=AssignmentOut,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Accept a delivery assignment",
    description="Assigns the order to the rider, moves it to RIDER_ASSIGNED and marks the rider `on_delivery`.",
)
async def accept_assignment(assignment_id: uuid.UUID, user: RiderUser, service: Service) -> AssignmentOut:
    return AssignmentOut.model_validate(await service.accept(user, assignment_id))


@router.post(
    "/assignments/{assignment_id}/reject",
    response_model=AssignmentOut,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Reject a delivery assignment",
)
async def reject_assignment(
    assignment_id: uuid.UUID,
    user: RiderUser,
    service: Service,
    payload: Annotated[AssignmentRejectRequest | None, Body()] = None,
) -> AssignmentOut:
    return AssignmentOut.model_validate(await service.reject(user, assignment_id, payload.reason if payload else None))


@router.get(
    "/{rider_id}",
    response_model=RiderOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Get a rider (self, admin, or vendors for approved riders)",
)
async def get_rider(rider_id: uuid.UUID, user: CurrentUser, service: Service) -> RiderOut:
    return RiderOut.model_validate(await service.get(rider_id, user))


@router.put(
    "/{rider_id}",
    response_model=RiderOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Update rider vehicle details (self or admin)",
)
async def update_rider(rider_id: uuid.UUID, payload: RiderUpdate, user: RiderOrAdmin, service: Service) -> RiderOut:
    return RiderOut.model_validate(await service.update(rider_id, user, payload))
