import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, status

from app.api.deps import CurrentUser, CustomerUser, PaginationDep, get_order_service
from app.models.enums import OrderStatus
from app.schemas.common import AUTH_ERRORS, Page, error_responses
from app.schemas.order import (
    CheckoutCreate,
    CheckoutResponse,
    OrderCancelRequest,
    OrderDetailOut,
    OrderOut,
    OrderStatusUpdate,
)
from app.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["Orders"])
Service = Annotated[OrderService, Depends(get_order_service)]


@router.post(
    "",
    response_model=CheckoutResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(404, 409, *AUTH_ERRORS),
    summary="Checkout a (multi-vendor) cart",
    description="Creates one order per vendor inside a single database transaction; all orders share a "
    "`checkout_reference`. Send an `Idempotency-Key` header to make retries safe: repeating a request "
    "with the same key returns the original checkout instead of creating duplicates.",
)
async def create_order(
    payload: CheckoutCreate,
    customer: CustomerUser,
    service: Service,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=100)] = None,
) -> CheckoutResponse:
    return await service.checkout(customer, payload, idempotency_key)


@router.get(
    "",
    response_model=Page[OrderOut],
    responses=error_responses(*AUTH_ERRORS),
    summary="Order history",
    description="Customers see their orders, vendors their shop's orders, riders their deliveries, admins all "
    "(admins may also filter by `vendor_id` / `customer_id`).",
)
async def list_orders(
    user: CurrentUser,
    service: Service,
    page: PaginationDep,
    status_: Annotated[OrderStatus | None, Query(alias="status")] = None,
    checkout_reference: Annotated[str | None, Query(max_length=40)] = None,
    vendor_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Page[OrderOut]:
    items, total = await service.list_orders(
        user, status=status_, checkout_reference=checkout_reference, vendor_id=vendor_id,
        customer_id=customer_id, date_from=date_from, date_to=date_to, limit=page.limit, offset=page.offset,
    )
    return Page(items=[OrderOut.model_validate(o) for o in items], total=total, limit=page.limit, offset=page.offset)


@router.get(
    "/{order_id}",
    response_model=OrderDetailOut,
    responses=error_responses(404, *AUTH_ERRORS),
    summary="Order details with items and status timeline",
)
async def get_order(order_id: uuid.UUID, user: CurrentUser, service: Service) -> OrderDetailOut:
    return OrderDetailOut.model_validate(await service.get_detail(order_id, user))


@router.put(
    "/{order_id}/status",
    response_model=OrderDetailOut,
    responses=error_responses(400, 404, 409, *AUTH_ERRORS),
    summary="Advance the order status",
    description="Valid progression: ORDER_PLACED → VENDOR_ACCEPTED → PREPARING → READY_FOR_PICKUP → "
    "RIDER_ASSIGNED → PICKED_UP → OUT_FOR_DELIVERY → DELIVERED. Vendors drive the first three steps, the "
    "assigned rider the last three; RIDER_ASSIGNED is set when a rider accepts an assignment. Invalid "
    "transitions return 409 `INVALID_STATUS_TRANSITION`.",
)
async def update_order_status(
    order_id: uuid.UUID, payload: OrderStatusUpdate, user: CurrentUser, service: Service
) -> OrderDetailOut:
    order = await service.update_status(order_id, user, payload.status, payload.description)
    return OrderDetailOut.model_validate(order)


@router.post(
    "/{order_id}/cancel",
    response_model=OrderDetailOut,
    responses=error_responses(403, 404, 409, 401, 422),
    summary="Cancel an order",
    description="Customers may cancel until the vendor accepts; vendors and admins until pickup.",
)
async def cancel_order(
    order_id: uuid.UUID,
    user: CurrentUser,
    service: Service,
    payload: Annotated[OrderCancelRequest | None, Body()] = None,
) -> OrderDetailOut:
    reason = payload.reason if payload else None
    return OrderDetailOut.model_validate(await service.cancel(order_id, user, reason))
