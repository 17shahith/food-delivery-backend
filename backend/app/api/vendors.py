import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AdminUser, OptionalUser, PaginationDep, VendorOrAdmin, get_vendor_service
from app.models.enums import ApprovalStatus
from app.schemas.common import AUTH_ERRORS, Page, error_responses
from app.schemas.vendor import VendorAvailabilityUpdate, VendorCreate, VendorOut, VendorStatusUpdate, VendorUpdate
from app.services.vendor_service import VendorService

router = APIRouter(prefix="/vendors", tags=["Vendors"])
Service = Annotated[VendorService, Depends(get_vendor_service)]


@router.post(
    "",
    response_model=VendorOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 409, *AUTH_ERRORS),
    summary="Create a shop",
    description="Vendors create their own shop (status `pending`). Admins may create one for a vendor "
    "user by passing `user_id` (status `approved`).",
)
async def create_vendor(payload: VendorCreate, user: VendorOrAdmin, service: Service) -> VendorOut:
    return VendorOut.model_validate(await service.create(user, payload))


@router.get(
    "",
    response_model=Page[VendorOut],
    responses=error_responses(400, 401, 422),
    summary="List / search / find nearby vendors",
    description="Public. Non-admins only see approved shops. Pass `latitude` + `longitude` (and optional "
    "`radius_km`) for a nearby search sorted by distance.",
)
async def list_vendors(
    viewer: OptionalUser,
    service: Service,
    page: PaginationDep,
    q: Annotated[str | None, Query(max_length=100, description="Search shop name/description")] = None,
    status_: Annotated[ApprovalStatus | None, Query(alias="status", description="Admin only")] = None,
    is_open: bool | None = None,
    latitude: Annotated[float | None, Query(ge=-90, le=90)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float | None, Query(gt=0, le=50)] = None,
) -> Page[VendorOut]:
    items, total = await service.list_vendors(
        viewer, q=q, status=status_, is_open=is_open, latitude=latitude, longitude=longitude,
        radius_km=radius_km, limit=page.limit, offset=page.offset,
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/me",
    response_model=VendorOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="The current vendor's own shop",
)
async def my_vendor(user: VendorOrAdmin, service: Service) -> VendorOut:
    return VendorOut.model_validate(await service.get_own(user))


@router.get("/{vendor_id}", response_model=VendorOut, responses=error_responses(401, 404), summary="Get a vendor")
async def get_vendor(vendor_id: uuid.UUID, viewer: OptionalUser, service: Service) -> VendorOut:
    return VendorOut.model_validate(await service.get(vendor_id, viewer))


@router.put(
    "/{vendor_id}",
    response_model=VendorOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Update shop profile (owner or admin)",
)
async def update_vendor(vendor_id: uuid.UUID, payload: VendorUpdate, user: VendorOrAdmin, service: Service) -> VendorOut:
    return VendorOut.model_validate(await service.update(vendor_id, user, payload))


@router.delete(
    "/{vendor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Delete a shop without order history (owner or admin)",
)
async def delete_vendor(vendor_id: uuid.UUID, user: VendorOrAdmin, service: Service) -> Response:
    await service.delete(vendor_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/{vendor_id}/status",
    response_model=VendorOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Approve, reject or suspend a vendor (admin)",
)
async def set_vendor_status(
    vendor_id: uuid.UUID, payload: VendorStatusUpdate, admin: AdminUser, service: Service
) -> VendorOut:
    return VendorOut.model_validate(await service.set_status(vendor_id, payload, admin))


@router.patch(
    "/{vendor_id}/availability",
    response_model=VendorOut,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Open or close the shop for orders (owner or admin)",
)
async def set_vendor_availability(
    vendor_id: uuid.UUID, payload: VendorAvailabilityUpdate, user: VendorOrAdmin, service: Service
) -> VendorOut:
    return VendorOut.model_validate(await service.set_availability(vendor_id, payload.is_open, user))
