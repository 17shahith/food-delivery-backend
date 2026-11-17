from app.models.enums import UserRole
from tests.conftest import auth_headers, place_order, set_status


async def test_order_lifecycle_generates_notifications(client, delivery, customer, vendor_user, rider_user):
    order_id = delivery["order_id"]
    for status in ("PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED"):
        await set_status(client, rider_user, order_id, status)

    customer_types = {n["type"] for n in (await client.get("/api/notifications", params={"limit": 100}, headers=auth_headers(customer))).json()["items"]}
    assert customer_types >= {
        "order_order_placed", "order_vendor_accepted", "order_preparing", "order_ready_for_pickup",
        "order_rider_assigned", "order_picked_up", "order_out_for_delivery", "order_delivered",
    }

    vendor_types = {n["type"] for n in (await client.get("/api/notifications", params={"limit": 100}, headers=auth_headers(vendor_user))).json()["items"]}
    assert vendor_types >= {"order_new", "order_rider_assigned", "order_picked_up", "order_delivered"}

    rider_types = {n["type"] for n in (await client.get("/api/notifications", headers=auth_headers(rider_user))).json()["items"]}
    assert "assignment_offered" in rider_types


async def test_cancellation_notifies_vendor(client, factory, customer, vendor_user, shop):
    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    await client.post(f"/api/orders/{order_id}/cancel", headers=auth_headers(customer))
    notes = (await client.get("/api/notifications", headers=auth_headers(vendor_user))).json()["items"]
    assert any(n["type"] == "order_cancelled" for n in notes)


async def test_read_flow_and_privacy(client, factory, customer, vendor_user, shop):
    product = await factory.product(shop)
    await place_order(client, customer, [(product, 1)])
    headers = auth_headers(customer)

    listing = (await client.get("/api/notifications", headers=headers)).json()
    assert listing["unread_count"] == 1 and listing["total"] == 1
    note_id = listing["items"][0]["id"]

    assert (await client.get(f"/api/notifications/{note_id}", headers=headers)).status_code == 200

    # Another user cannot read or mark it.
    assert (await client.get(f"/api/notifications/{note_id}", headers=auth_headers(vendor_user))).status_code == 404
    assert (await client.put(f"/api/notifications/{note_id}/read", headers=auth_headers(vendor_user))).status_code == 404

    marked = await client.put(f"/api/notifications/{note_id}/read", headers=headers)
    assert marked.status_code == 200 and marked.json()["is_read"] is True
    assert (await client.get("/api/notifications", headers=headers)).json()["unread_count"] == 0

    unread_filter = await client.get("/api/notifications", params={"is_read": False}, headers=auth_headers(vendor_user))
    assert unread_filter.json()["total"] == 1
    read_all = await client.put("/api/notifications/read-all", headers=auth_headers(vendor_user))
    assert read_all.json()["message"].startswith("1 ")


async def test_admin_announcements(client, factory, admin):
    customers = [await factory.user(UserRole.CUSTOMER) for _ in range(3)]
    await factory.user(UserRole.CUSTOMER, is_active=False)

    broadcast = await client.post(
        "/api/notifications", json={"role": "customer", "title": "Festival sale", "message": "20% off"},
        headers=auth_headers(admin),
    )
    assert broadcast.status_code == 201 and broadcast.json()["created"] == 3

    direct = await client.post(
        "/api/notifications", json={"user_id": str(customers[0].id), "title": "Hi", "message": "Welcome"},
        headers=auth_headers(admin),
    )
    assert direct.json()["created"] == 1
    assert (await client.get("/api/notifications", headers=auth_headers(customers[0]))).json()["total"] == 2

    invalid = await client.post(
        "/api/notifications", json={"title": "No target", "message": "x"}, headers=auth_headers(admin)
    )
    assert invalid.status_code == 422
