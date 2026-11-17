"""Orders (customer + vendor actions) and rider deliveries for the web app."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, status

from app.api.client.deps import Client, ensure_app_order_access
from app.api.deps import CurrentUser, CustomerUser, RiderUser, get_order_service, get_rider_service
from app.models.enums import ApprovalStatus, OrderStatus, PaymentMethod, RiderAvailability
from app.schemas.client import AppOrder, AppRider, OnlineIn, OrderCreateIn, OrderStatusIn, ReasonIn, RiderPatch, RiderStatsOut
from app.schemas.common import error_responses
from app.schemas.order import CheckoutCreate, OrderItemCreate
from app.schemas.rider import RiderAvailabilityUpdate, RiderUpdate
from app.services.client_mappers import domain_status, domain_vehicle, parse_status_list, to_app_order, to_app_rider
from app.services.order_service import OrderService
from app.services.rider_service import RiderService
from app.utils.exceptions import NotFoundError

router = APIRouter()
Orders = Annotated[OrderService, Depends(get_order_service)]
Riders = Annotated[RiderService, Depends(get_rider_service)]
ORDER_ERRORS = error_responses(400, 401, 403, 404, 409, 422)


# ----------------------------------------------------------------------------- customer
@router.post("/orders", response_model=AppOrder, status_code=status.HTTP_201_CREATED, tags=["App: Orders"],
             responses=ORDER_ERRORS, summary="Place an order at one shop (server computes prices)")
async def create_order(
    payload: OrderCreateIn,
    customer: CustomerUser,
    orders: Orders,
    client: Client,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=100)] = None,
) -> AppOrder:
    address = await client.get_address(customer, payload.address_id)
    vendor = await orders.vendors.get(payload.vendor_id)
    if vendor is None or vendor.status != ApprovalStatus.APPROVED:
        raise NotFoundError("Shop not found")
    checkout = await orders.checkout(
        customer,
        CheckoutCreate(
            items=[OrderItemCreate(product_id=i.product_id, quantity=i.quantity) for i in payload.items],
            delivery_address=address.formatted(),
            delivery_latitude=address.latitude,
            delivery_longitude=address.longitude,
            notes=payload.notes,
        ),
        idempotency_key,
        payment_method=PaymentMethod(payload.payment_method),
        delivery_details=address.snapshot(),
        only_vendor_id=vendor.id,
    )
    return to_app_order(await client.load_order(checkout.orders[0].id))


@router.get("/orders/me", response_model=list[AppOrder], tags=["App: Orders"])
async def my_orders(
    customer: CustomerUser,
    client: Client,
    status_: Annotated[str | None, Query(alias="status", description="Comma-separated statuses")] = None,
) -> list[AppOrder]:
    return [to_app_order(o) for o in await client.list_orders(customer_id=customer.id, statuses=parse_status_list(status_))]


@router.get("/orders/{order_id}", response_model=AppOrder, tags=["App: Orders"], responses=error_responses(401, 404))
async def get_order(order_id: uuid.UUID, user: CurrentUser, client: Client) -> AppOrder:
    order = await client.load_order(order_id)
    await ensure_app_order_access(client, order, user)
    return to_app_order(order)


@router.post("/orders/{order_id}/cancel", response_model=AppOrder, tags=["App: Orders"], responses=ORDER_ERRORS)
async def cancel_order(
    order_id: uuid.UUID, user: CurrentUser, orders: Orders, client: Client,
    payload: Annotated[ReasonIn | None, Body()] = None,
) -> AppOrder:
    await orders.cancel(order_id, user, payload.reason if payload else None)
    return to_app_order(await client.load_order(order_id))


# ----------------------------------------------------------------------------- vendor actions
@router.post("/orders/{order_id}/accept", response_model=AppOrder, tags=["App: Orders"], responses=ORDER_ERRORS)
async def accept_order(order_id: uuid.UUID, user: CurrentUser, orders: Orders, client: Client) -> AppOrder:
    await orders.update_status(order_id, user, OrderStatus.VENDOR_ACCEPTED, None)
    return to_app_order(await client.load_order(order_id))


@router.post("/orders/{order_id}/reject", response_model=AppOrder, tags=["App: Orders"], responses=ORDER_ERRORS)
async def reject_order(
    order_id: uuid.UUID, user: CurrentUser, orders: Orders, client: Client,
    payload: Annotated[ReasonIn | None, Body()] = None,
) -> AppOrder:
    await orders.update_status(order_id, user, OrderStatus.REJECTED, (payload.reason if payload else None) or None)
    return to_app_order(await client.load_order(order_id))


@router.patch("/orders/{order_id}/status", response_model=AppOrder, tags=["App: Orders"], responses=ORDER_ERRORS,
              summary="Vendor: accepted → preparing → ready_for_pickup")
async def update_order_status(
    order_id: uuid.UUID, payload: OrderStatusIn, user: CurrentUser, orders: Orders, client: Client
) -> AppOrder:
    await orders.update_status(order_id, user, domain_status(payload.status), None)
    return to_app_order(await client.load_order(order_id))


# ----------------------------------------------------------------------------- rider profile
async def _rider_out(client, rider) -> AppRider:
    counts = await client.delivered_counts([rider.id])
    return to_app_rider(rider, total_deliveries=counts.get(rider.id, 0), private_user=True)


@router.get("/riders/me", response_model=AppRider, tags=["App: Riders"], responses=error_responses(401, 403, 404))
async def my_rider_profile(user: RiderUser, client: Client) -> AppRider:
    return await _rider_out(client, await client.own_rider(user))


@router.patch("/riders/me", response_model=AppRider, tags=["App: Riders"], responses=error_responses(401, 403, 404, 422))
async def update_my_rider_profile(payload: RiderPatch, user: RiderUser, client: Client, riders: Riders) -> AppRider:
    rider = await client.own_rider(user)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("vehicle_type"):
        changes["vehicle_type"] = domain_vehicle(changes["vehicle_type"])
    if "vehicle_number" in changes and not changes["vehicle_number"]:
        changes["vehicle_number"] = None
    rider = await riders.update(rider.id, user, RiderUpdate(**changes))
    return await _rider_out(client, rider)


@router.patch("/riders/me/availability", response_model=AppRider, tags=["App: Riders"],
              responses=error_responses(401, 403, 404, 409))
async def set_online(payload: OnlineIn, user: RiderUser, client: Client, riders: Riders) -> AppRider:
    rider = await client.own_rider(user)
    if payload.is_online and rider.availability == RiderAvailability.ON_DELIVERY:
        return await _rider_out(client, rider)  # already online (busy)
    target = RiderAvailability.ONLINE if payload.is_online else RiderAvailability.OFFLINE
    rider = await riders.set_availability(user, RiderAvailabilityUpdate(availability=target))
    return await _rider_out(client, rider)


@router.get("/riders/me/stats", response_model=RiderStatsOut, tags=["App: Riders"])
async def my_rider_stats(user: RiderUser, client: Client) -> RiderStatsOut:
    return await client.rider_stats(await client.own_rider(user))


@router.get("/riders/me/deliveries", response_model=list[AppOrder], tags=["App: Riders"])
async def my_deliveries(
    user: RiderUser,
    client: Client,
    status_: Annotated[str | None, Query(alias="status", description="Comma-separated statuses")] = None,
) -> list[AppOrder]:
    rider = await client.own_rider(user)
    return [to_app_order(o) for o in await client.list_orders(rider_id=rider.id, statuses=parse_status_list(status_))]


# ----------------------------------------------------------------------------- job board
@router.get("/deliveries/available", response_model=list[AppOrder], tags=["App: Riders"],
            summary="Ready orders this rider can take (empty while offline or unapproved)")
async def available_deliveries(user: RiderUser, riders: Riders) -> list[AppOrder]:
    return [to_app_order(order) for order, _distance in await riders.available_orders(user)]


@router.post("/deliveries/{order_id}/accept", response_model=AppOrder, tags=["App: Riders"], responses=ORDER_ERRORS)
async def accept_delivery(order_id: uuid.UUID, user: RiderUser, riders: Riders, client: Client) -> AppOrder:
    await riders.claim(user, order_id)
    return to_app_order(await client.load_order(order_id))


@router.post("/deliveries/{order_id}/reject", status_code=status.HTTP_204_NO_CONTENT, tags=["App: Riders"],
             responses=error_responses(401, 403, 404))
async def skip_delivery(
    order_id: uuid.UUID, user: RiderUser, riders: Riders, payload: Annotated[ReasonIn | None, Body()] = None
) -> None:
    await riders.skip(user, order_id, payload.reason if payload else None)


@router.patch("/deliveries/{order_id}/status", response_model=AppOrder, tags=["App: Riders"], responses=ORDER_ERRORS,
              summary="Rider: picked_up → out_for_delivery → delivered")
async def update_delivery_status(
    order_id: uuid.UUID, payload: OrderStatusIn, user: RiderUser, orders: Orders, client: Client
) -> AppOrder:
    await orders.update_status(order_id, user, domain_status(payload.status), None)
    return to_app_order(await client.load_order(order_id))
