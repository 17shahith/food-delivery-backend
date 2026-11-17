"""Tracking, notifications and administration for the web app."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Response, status

from app.api.client.deps import Client
from app.api.deps import (
    AdminUser,
    CurrentUser,
    RiderUser,
    get_admin_service,
    get_notification_service,
    get_rider_service,
    get_tracking_service,
    get_vendor_service,
)
from app.models.enums import TRACKABLE_ORDER_STATUSES, ApprovalStatus, UserRole
from app.schemas.admin import AdminUserUpdate
from app.schemas.client import (
    ActiveIn,
    AdminStatsOut,
    AnalyticsOut,
    AppNotification,
    AppOrder,
    AppRider,
    AppRiderLocation,
    AppUser,
    AppVendor,
    MessageOut,
    ReasonIn,
)
from app.schemas.common import error_responses
from app.schemas.rider import RiderStatusUpdate
from app.schemas.tracking import IdleLocationUpdate, LocationUpdateResponse
from app.schemas.vendor import VendorStatusUpdate
from app.services.admin_service import AdminService
from app.services.client_mappers import (
    domain_status,
    to_app_location,
    to_app_notification,
    to_app_order,
    to_app_rider,
    to_app_user,
    to_app_vendor,
)
from app.services.notification_service import NotificationService
from app.services.order_workflow import can_view_order
from app.services.rider_service import RiderService
from app.services.tracking_service import TrackingService
from app.services.vendor_service import VendorService
from app.utils.exceptions import ConflictError, NotFoundError

router = APIRouter()
Tracking = Annotated[TrackingService, Depends(get_tracking_service)]
Notifications = Annotated[NotificationService, Depends(get_notification_service)]
Admin = Annotated[AdminService, Depends(get_admin_service)]
Vendors = Annotated[VendorService, Depends(get_vendor_service)]
Riders = Annotated[RiderService, Depends(get_rider_service)]


# ----------------------------------------------------------------------------- tracking
@router.post(
    "/tracking/location",
    response_model=LocationUpdateResponse,
    tags=["App: Tracking"],
    responses=error_responses(401, 403, 404, 422),
    summary="Rider GPS fix (order_id optional); stored in rider_locations and streamed via Realtime",
)
async def send_location(payload: IdleLocationUpdate, user: RiderUser, tracking: Tracking) -> LocationUpdateResponse:
    try:
        return await tracking.update_location(user, payload)
    except ConflictError as exc:
        if exc.code != "TRACKING_NOT_ACTIVE":
            raise
        # The delivery just ended (or hasn't started): keep the rider's own position fresh instead.
        return await tracking.update_location(user, payload.model_copy(update={"order_id": None}))


@router.get(
    "/tracking/orders/{order_id}",
    response_model=AppRiderLocation | None,
    tags=["App: Tracking"],
    responses=error_responses(401, 404),
    summary="Latest rider position for an order (null when not available)",
)
async def order_location(order_id: uuid.UUID, user: CurrentUser, client: Client) -> AppRiderLocation | None:
    order = await client.load_order(order_id)
    if not can_view_order(order, user):
        raise NotFoundError("Order not found")
    if user.role == UserRole.CUSTOMER and order.order_status not in TRACKABLE_ORDER_STATUSES:
        return None  # customers only see the rider while the delivery is live
    point = await client.latest_order_location(order)
    if point is None:
        return None
    return to_app_location(point, rider_name=order.rider.user.name if order.rider else None)


@router.get("/tracking/active", response_model=list[AppRiderLocation], tags=["App: Tracking"])
async def active_locations(_: AdminUser, client: Client) -> list[AppRiderLocation]:
    return [to_app_location(loc, rider_name=name) for loc, name in await client.active_locations()]


# ----------------------------------------------------------------------------- notifications
@router.get("/notifications", response_model=list[AppNotification], tags=["App: Notifications"])
async def list_notifications(user: CurrentUser, client: Client) -> list[AppNotification]:
    return [to_app_notification(n) for n in await client.notifications(user)]


@router.post("/notifications/read-all", response_model=MessageOut, tags=["App: Notifications"])
async def read_all(user: CurrentUser, notifications: Notifications) -> MessageOut:
    count = await notifications.mark_all_read(user.id)
    return MessageOut(message=f"{count} notification(s) marked as read")


@router.patch("/notifications/{notification_id}/read", response_model=AppNotification, tags=["App: Notifications"],
              responses=error_responses(401, 404))
async def mark_read(notification_id: uuid.UUID, user: CurrentUser, notifications: Notifications) -> AppNotification:
    return to_app_notification(await notifications.mark_read(notification_id, user))


@router.delete("/notifications/{notification_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["App: Notifications"])
async def delete_notification(notification_id: uuid.UUID, user: CurrentUser, client: Client) -> Response:
    await client.delete_notification(user, notification_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------------------- admin
@router.get("/admin/stats", response_model=AdminStatsOut, tags=["App: Admin"])
async def admin_stats(_: AdminUser, client: Client) -> AdminStatsOut:
    return await client.admin_stats()


@router.get("/admin/users", response_model=list[AppUser], tags=["App: Admin"])
async def admin_users(
    _: AdminUser,
    client: Client,
    role: UserRole | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    status_: Annotated[Literal["active", "inactive"] | None, Query(alias="status")] = None,
) -> list[AppUser]:
    users = await client.admin_users(role=role, search=search, active=None if status_ is None else status_ == "active")
    approvals = await client.approvals_for(users)
    return [to_app_user(u, approvals[u.id]) for u in users]


@router.patch("/admin/users/{user_id}/status", response_model=AppUser, tags=["App: Admin"],
              responses=error_responses(400, 404))
async def set_user_active(user_id: uuid.UUID, payload: ActiveIn, admin: AdminUser, service: Admin, client: Client) -> AppUser:
    user = await service.update_user(user_id, AdminUserUpdate(is_active=payload.is_active), admin)
    return to_app_user(user, await client.approval_for(user))


@router.get("/admin/vendors", response_model=list[AppVendor], tags=["App: Admin"])
async def admin_vendors(
    _: AdminUser,
    client: Client,
    search: Annotated[str | None, Query(max_length=100)] = None,
    status_: Annotated[Literal["pending", "approved", "rejected"] | None, Query(alias="status")] = None,
) -> list[AppVendor]:
    vendors = await client.admin_vendors(search=search, status=ApprovalStatus(status_) if status_ else None)
    return [to_app_vendor(v, owner=v.user) for v in vendors]


@router.get("/admin/riders", response_model=list[AppRider], tags=["App: Admin"])
async def admin_riders(
    _: AdminUser,
    client: Client,
    search: Annotated[str | None, Query(max_length=100)] = None,
    status_: Annotated[Literal["pending", "approved", "rejected"] | None, Query(alias="status")] = None,
) -> list[AppRider]:
    riders = await client.admin_riders(search=search, status=ApprovalStatus(status_) if status_ else None)
    counts = await client.delivered_counts([r.id for r in riders])
    return [to_app_rider(r, total_deliveries=counts.get(r.id, 0), private_user=True) for r in riders]


def _decision(approve: bool, payload: ReasonIn | None):
    return (ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED), (payload.reason if payload else None)


@router.post("/admin/vendors/{vendor_id}/approve", response_model=AppVendor, tags=["App: Admin"], responses=error_responses(404))
async def approve_vendor(vendor_id: uuid.UUID, admin: AdminUser, vendors: Vendors,
                         payload: Annotated[ReasonIn | None, Body()] = None) -> AppVendor:
    status_, reason = _decision(True, payload)
    return to_app_vendor(await vendors.set_status(vendor_id, VendorStatusUpdate(status=status_, reason=reason), admin))


@router.post("/admin/vendors/{vendor_id}/reject", response_model=AppVendor, tags=["App: Admin"], responses=error_responses(404))
async def reject_vendor(vendor_id: uuid.UUID, admin: AdminUser, vendors: Vendors,
                        payload: Annotated[ReasonIn | None, Body()] = None) -> AppVendor:
    status_, reason = _decision(False, payload)
    return to_app_vendor(await vendors.set_status(vendor_id, VendorStatusUpdate(status=status_, reason=reason), admin))


@router.post("/admin/riders/{rider_id}/approve", response_model=AppRider, tags=["App: Admin"], responses=error_responses(404, 409))
async def approve_rider(rider_id: uuid.UUID, _: AdminUser, riders: Riders, client: Client,
                        payload: Annotated[ReasonIn | None, Body()] = None) -> AppRider:
    status_, reason = _decision(True, payload)
    rider = await riders.set_status(rider_id, RiderStatusUpdate(status=status_, reason=reason))
    counts = await client.delivered_counts([rider.id])
    return to_app_rider(rider, total_deliveries=counts.get(rider.id, 0), private_user=True)


@router.post("/admin/riders/{rider_id}/reject", response_model=AppRider, tags=["App: Admin"], responses=error_responses(404, 409))
async def reject_rider(rider_id: uuid.UUID, _: AdminUser, riders: Riders, client: Client,
                       payload: Annotated[ReasonIn | None, Body()] = None) -> AppRider:
    status_, reason = _decision(False, payload)
    rider = await riders.set_status(rider_id, RiderStatusUpdate(status=status_, reason=reason))
    counts = await client.delivered_counts([rider.id])
    return to_app_rider(rider, total_deliveries=counts.get(rider.id, 0), private_user=True)


@router.get("/admin/orders", response_model=list[AppOrder], tags=["App: Admin"])
async def admin_orders(
    _: AdminUser,
    client: Client,
    status_: Annotated[str | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> list[AppOrder]:
    statuses = [domain_status(status_)] if status_ else None
    return [to_app_order(o) for o in await client.list_orders(statuses=statuses, search=search)]


@router.get("/admin/analytics", response_model=AnalyticsOut, tags=["App: Admin"])
async def admin_analytics(_: AdminUser, client: Client, days: Annotated[int, Query(ge=1, le=365)] = 30) -> AnalyticsOut:
    return await client.analytics(days)
