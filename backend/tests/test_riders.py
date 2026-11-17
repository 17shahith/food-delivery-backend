import uuid
from datetime import timedelta

from sqlalchemy import select, update

from app.models import Order, Rider, RiderAssignment
from app.models.enums import ApprovalStatus, RiderAvailability, UserRole
from app.utils.helpers import utcnow
from tests.conftest import advance_to_ready, assign_and_accept, auth_headers, place_order


async def _fresh(db, model, id_):
    return (await db.execute(select(model).where(model.id == id_).execution_options(populate_existing=True))).scalar_one()


async def _ready_order(client, factory, customer, vendor_user, shop) -> str:
    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    await advance_to_ready(client, vendor_user, order_id)
    return order_id


async def test_rider_profile_lifecycle(client, factory, admin):
    user = await factory.user(UserRole.RIDER)
    headers = auth_headers(user)
    created = await client.post("/api/riders", json={"vehicle_type": "bicycle"}, headers=headers)
    assert created.status_code == 201, created.text
    rider = created.json()
    assert rider["status"] == "pending" and rider["availability"] == "offline" and rider["name"] == user.name

    assert (await client.post("/api/riders", json={"vehicle_type": "car"}, headers=headers)).status_code == 409

    # Pending riders cannot go online.
    online = await client.put("/api/riders/availability", json={"availability": "online"}, headers=headers)
    assert online.status_code == 409 and online.json()["error"]["code"] == "RIDER_NOT_APPROVED"

    approved = await client.patch(
        f"/api/admin/riders/{rider['id']}/approval", json={"status": "approved"}, headers=auth_headers(admin)
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"

    online = await client.put(
        "/api/riders/availability", json={"availability": "online", "latitude": 12.97, "longitude": 77.59}, headers=headers
    )
    assert online.status_code == 200 and online.json()["availability"] == "online"
    assert online.json()["latitude"] == 12.97

    updated = await client.put(f"/api/riders/{rider['id']}", json={"vehicle_number": "KA05XY9999"}, headers=headers)
    assert updated.status_code == 200 and updated.json()["vehicle_number"] == "KA05XY9999"

    assert (await client.get("/api/riders/me", headers=headers)).json()["id"] == rider["id"]
    assert (await client.get(f"/api/riders/{rider['id']}", headers=headers)).status_code == 200
    listing = await client.get("/api/riders", params={"status": "approved"}, headers=auth_headers(admin))
    assert listing.json()["total"] == 1

    # Cannot set on_delivery manually.
    manual = await client.put("/api/riders/availability", json={"availability": "on_delivery"}, headers=headers)
    assert manual.status_code == 422


async def test_rider_cannot_view_or_edit_other_rider(client, factory, rider):
    other = await factory.user(UserRole.RIDER)
    await factory.rider(other)
    assert (await client.get(f"/api/riders/{rider.id}", headers=auth_headers(other))).status_code == 404
    assert (await client.put(f"/api/riders/{rider.id}", json={"vehicle_number": "X"}, headers=auth_headers(other))).status_code == 404


async def test_find_nearby_available_riders(client, factory, vendor_user, shop, customer):
    near = await factory.rider(latitude=12.972, longitude=77.595)
    farther = await factory.rider(latitude=12.99, longitude=77.60)
    await factory.rider(latitude=13.5, longitude=77.6)                                   # out of radius
    await factory.rider(availability=RiderAvailability.OFFLINE)                          # offline
    await factory.rider(status=ApprovalStatus.PENDING)                                   # not approved
    stale = await factory.rider(latitude=12.9717, longitude=77.5947)
    stale.last_location_at = utcnow() - timedelta(hours=2)
    await factory.db.commit()

    response = await client.get(
        "/api/riders/available", params={"latitude": 12.9716, "longitude": 77.5946, "radius_km": 5},
        headers=auth_headers(vendor_user),
    )
    assert response.status_code == 200
    assert [r["id"] for r in response.json()] == [str(near.id), str(farther.id)]

    # Search around an order's shop.
    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    by_order = await client.get("/api/riders/available", params={"order_id": order_id}, headers=auth_headers(vendor_user))
    assert by_order.json()[0]["id"] == str(near.id)

    assert (await client.get("/api/riders/available", headers=auth_headers(vendor_user))).status_code == 400


async def test_assign_accept_flow(client, factory, customer, vendor_user, shop, rider_user, rider, db, publisher):
    order_id = await _ready_order(client, factory, customer, vendor_user, shop)
    assignment = await assign_and_accept(client, vendor_user, rider_user, order_id, rider.id)
    assert assignment["status"] == "accepted" and assignment["responded_at"]

    order = await _fresh(db, Order, uuid.UUID(order_id))
    assert order.order_status.value == "RIDER_ASSIGNED" and order.rider_id == rider.id
    assert (await _fresh(db, Rider, rider.id)).availability == RiderAvailability.ON_DELIVERY

    # Rider is now busy and can't go offline mid-delivery.
    offline = await client.put("/api/riders/availability", json={"availability": "offline"}, headers=auth_headers(rider_user))
    assert offline.status_code == 409

    # Rider sees the delivery in their order list.
    orders = await client.get("/api/orders", headers=auth_headers(rider_user))
    assert orders.json()["total"] == 1

    # Customer was notified about the rider.
    notes = (await client.get("/api/notifications", headers=auth_headers(customer))).json()["items"]
    assert any(n["type"] == "order_rider_assigned" for n in notes)


async def test_assignment_requires_ready_order(client, factory, customer, vendor_user, shop, rider):
    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    response = await client.post(
        "/api/riders/assign", json={"order_id": order_id, "rider_id": str(rider.id)}, headers=auth_headers(vendor_user)
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "ORDER_NOT_READY"


async def test_conflicting_assignments_prevented(client, factory, customer, vendor_user, shop, rider_user, rider):
    order_a = await _ready_order(client, factory, customer, vendor_user, shop)
    order_b = await _ready_order(client, factory, customer, vendor_user, shop)
    second_rider = await factory.rider()
    headers = auth_headers(vendor_user)

    first = await client.post("/api/riders/assign", json={"order_id": order_a, "rider_id": str(rider.id)}, headers=headers)
    assert first.status_code == 201

    # Same order offered to another rider while pending -> conflict.
    dup_order = await client.post("/api/riders/assign", json={"order_id": order_a, "rider_id": str(second_rider.id)}, headers=headers)
    assert dup_order.status_code == 409 and dup_order.json()["error"]["code"] == "ASSIGNMENT_PENDING"

    # Same rider offered a second order while considering the first -> conflict.
    dup_rider = await client.post("/api/riders/assign", json={"order_id": order_b, "rider_id": str(rider.id)}, headers=headers)
    assert dup_rider.status_code == 409 and dup_rider.json()["error"]["code"] == "RIDER_HAS_PENDING_OFFER"

    # After accepting order A the rider is on delivery and cannot be offered B.
    await client.post(f"/api/riders/assignments/{first.json()['id']}/accept", headers=auth_headers(rider_user))
    busy = await client.post("/api/riders/assign", json={"order_id": order_b, "rider_id": str(rider.id)}, headers=headers)
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "RIDER_UNAVAILABLE"

    # Order A is already assigned.
    again = await client.post("/api/riders/assign", json={"order_id": order_a, "rider_id": str(second_rider.id)}, headers=headers)
    assert again.status_code == 409


async def test_reject_and_reassign(client, factory, customer, vendor_user, shop, rider_user, rider):
    order_id = await _ready_order(client, factory, customer, vendor_user, shop)
    headers = auth_headers(vendor_user)
    offer = (await client.post("/api/riders/assign", json={"order_id": order_id, "rider_id": str(rider.id)}, headers=headers)).json()

    pending = await client.get("/api/riders/assignments", params={"status": "pending"}, headers=auth_headers(rider_user))
    assert pending.json()["total"] == 1

    rejected = await client.post(
        f"/api/riders/assignments/{offer['id']}/reject", json={"reason": "Too far"}, headers=auth_headers(rider_user)
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "Too far"

    # Accepting a rejected assignment is not possible.
    late = await client.post(f"/api/riders/assignments/{offer['id']}/accept", headers=auth_headers(rider_user))
    assert late.status_code == 409

    vendor_notes = (await client.get("/api/notifications", headers=headers)).json()["items"]
    assert any(n["type"] == "assignment_rejected" for n in vendor_notes)

    other_rider_user = await factory.user(UserRole.RIDER)
    other = await factory.rider(other_rider_user)
    await assign_and_accept(client, vendor_user, other_rider_user, order_id, other.id)


async def test_expired_offer_can_be_reassigned(client, factory, customer, vendor_user, shop, rider_user, rider, db):
    order_id = await _ready_order(client, factory, customer, vendor_user, shop)
    headers = auth_headers(vendor_user)
    offer = (await client.post("/api/riders/assign", json={"order_id": order_id, "rider_id": str(rider.id)}, headers=headers)).json()

    await db.execute(
        update(RiderAssignment).where(RiderAssignment.id == uuid.UUID(offer["id"])).values(created_at=utcnow() - timedelta(hours=1))
    )
    await db.commit()

    expired = await client.post(f"/api/riders/assignments/{offer['id']}/accept", headers=auth_headers(rider_user))
    assert expired.status_code == 409 and expired.json()["error"]["code"] == "ASSIGNMENT_EXPIRED"

    other = await factory.rider()
    reassigned = await client.post("/api/riders/assign", json={"order_id": order_id, "rider_id": str(other.id)}, headers=headers)
    assert reassigned.status_code == 201


async def test_rider_cannot_accept_someone_elses_assignment(client, factory, customer, vendor_user, shop, rider):
    order_id = await _ready_order(client, factory, customer, vendor_user, shop)
    offer = (await client.post(
        "/api/riders/assign", json={"order_id": order_id, "rider_id": str(rider.id)}, headers=auth_headers(vendor_user)
    )).json()
    intruder = await factory.user(UserRole.RIDER)
    await factory.rider(intruder)
    response = await client.post(f"/api/riders/assignments/{offer['id']}/accept", headers=auth_headers(intruder))
    assert response.status_code == 404


async def test_other_vendor_cannot_assign(client, factory, customer, vendor_user, shop, rider):
    order_id = await _ready_order(client, factory, customer, vendor_user, shop)
    other = await factory.user(UserRole.VENDOR)
    await factory.vendor(other)
    response = await client.post(
        "/api/riders/assign", json={"order_id": order_id, "rider_id": str(rider.id)}, headers=auth_headers(other)
    )
    assert response.status_code == 404


async def test_delivered_order_frees_rider(client, delivery, rider_user, rider, db):
    for status in ("PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED"):
        await client.put(f"/api/orders/{delivery['order_id']}/status", json={"status": status}, headers=auth_headers(rider_user))
    assert (await _fresh(db, Rider, rider.id)).availability == RiderAvailability.ONLINE
