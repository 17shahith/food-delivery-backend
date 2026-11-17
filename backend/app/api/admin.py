"""Administrator-only endpoints. Every route depends on `AdminUser`."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    AdminUser,
    PaginationDep,
    get_admin_service,
    get_order_service,
    get_rider_service,
    get_vendor_service,
)
from app.models.enums import ApprovalStatus, OrderStatus, RiderAvailability, UserRole
from app.schemas.admin import AdminUserUpdate, LiveDelivery, PlatformAnalytics
from app.schemas.common import AUTH_ERRORS, Page, error_responses
from app.schemas.order import OrderOut
from app.schemas.rider import RiderOut, RiderStatusUpdate
from app.schemas.user import UserOut
from app.schemas.vendor import VendorOut, VendorStatusUpdate
from app.services.admin_service import AdminService
from app.services.order_service import OrderService
from app.services.rider_service import RiderService
from app.services.vendor_service import VendorService

router = APIRouter(prefix="/admin", tags=["Admin"], responses=error_responses(*AUTH_ERRORS))
Admin = Annotated[AdminService, Depends(get_admin_service)]


# ---------------------------------------------------------------- users (customers, vendors, riders)
@router.get("/users", response_model=Page[UserOut], summary="List / search users")
async def list_users(
    _: AdminUser,
    service: Admin,
    page: PaginationDep,
    role: UserRole | None = None,
    is_active: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[UserOut]:
    items, total = await service.list_users(role=role, is_active=is_active, q=q, limit=page.limit, offset=page.offset)
    return Page(items=[UserOut.model_validate(u) for u in items], total=total, limit=page.limit, offset=page.offset)


@router.get("/users/{user_id}", response_model=UserOut, responses=error_responses(404), summary="Get a user")
async def get_user(user_id: uuid.UUID, _: AdminUser, service: Admin) -> UserOut:
    return UserOut.model_validate(await service.get_user(user_id))


@router.patch(
    "/users/{user_id}",
    response_model=UserOut,
    responses=error_responses(400, 404, 409),
    summary="Activate / deactivate a user or change role",
)
async def update_user(user_id: uuid.UUID, payload: AdminUserUpdate, admin: AdminUser, service: Admin) -> UserOut:
    return UserOut.model_validate(await service.update_user(user_id, payload, admin))


# ---------------------------------------------------------------- vendors
@router.get("/vendors", response_model=Page[VendorOut], summary="List vendors in any status")
async def list_vendors(
    admin: AdminUser,
    page: PaginationDep,
    service: Annotated[VendorService, Depends(get_vendor_service)],
    status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[VendorOut]:
    items, total = await service.list_vendors(
        admin, q=q, status=status_, is_open=None, latitude=None, longitude=None, radius_km=None,
        limit=page.limit, offset=page.offset,
    )
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.patch(
    "/vendors/{vendor_id}/approval",
    response_model=VendorOut,
    responses=error_responses(404),
    summary="Approve / reject / suspend a vendor",
)
async def approve_vendor(
    vendor_id: uuid.UUID,
    payload: VendorStatusUpdate,
    admin: AdminUser,
    service: Annotated[VendorService, Depends(get_vendor_service)],
) -> VendorOut:
    return VendorOut.model_validate(await service.set_status(vendor_id, payload, admin))


# ---------------------------------------------------------------- riders
@router.get("/riders", response_model=Page[RiderOut], summary="List riders in any status")
async def list_riders(
    _: AdminUser,
    page: PaginationDep,
    service: Annotated[RiderService, Depends(get_rider_service)],
    status_: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    availability: RiderAvailability | None = None,
) -> Page[RiderOut]:
    items, total = await service.list_riders(status=status_, availability=availability, limit=page.limit, offset=page.offset)
    return Page(items=[RiderOut.model_validate(r) for r in items], total=total, limit=page.limit, offset=page.offset)


@router.patch(
    "/riders/{rider_id}/approval",
    response_model=RiderOut,
    responses=error_responses(404, 409),
    summary="Approve / reject / suspend a rider",
)
async def approve_rider(
    rider_id: uuid.UUID,
    payload: RiderStatusUpdate,
    _: AdminUser,
    service: Annotated[RiderService, Depends(get_rider_service)],
) -> RiderOut:
    return RiderOut.model_validate(await service.set_status(rider_id, payload))


# ---------------------------------------------------------------- monitoring
@router.get("/orders", response_model=Page[OrderOut], summary="Monitor all orders")
async def monitor_orders(
    admin: AdminUser,
    page: PaginationDep,
    service: Annotated[OrderService, Depends(get_order_service)],
    status_: Annotated[OrderStatus | None, Query(alias="status")] = None,
    vendor_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    checkout_reference: Annotated[str | None, Query(max_length=40)] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Page[OrderOut]:
    items, total = await service.list_orders(
        admin, status=status_, checkout_reference=checkout_reference, vendor_id=vendor_id, customer_id=customer_id,
        date_from=date_from, date_to=date_to, limit=page.limit, offset=page.offset,
    )
    return Page(items=[OrderOut.model_validate(o) for o in items], total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/deliveries/live",
    response_model=list[LiveDelivery],
    summary="Live deliveries with latest rider positions",
    description="Subscribe to Realtime topic `admin:deliveries` for streaming location and status events.",
)
async def live_deliveries(_: AdminUser, service: Admin) -> list[LiveDelivery]:
    return await service.live_deliveries()


@router.get("/analytics", response_model=PlatformAnalytics, summary="Platform analytics")
async def analytics(_: AdminUser, service: Admin) -> PlatformAnalytics:
    return await service.analytics()
