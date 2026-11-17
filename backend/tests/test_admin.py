from app.models.enums import ApprovalStatus, UserRole
from tests.conftest import auth_headers, place_order, set_status


async def test_user_management(client, factory, admin):
    customer = await factory.user(UserRole.CUSTOMER, name="Zara Customer")
    headers = auth_headers(admin)

    listing = await client.get("/api/admin/users", params={"role": "customer"}, headers=headers)
    assert listing.status_code == 200 and listing.json()["total"] == 1

    search = await client.get("/api/admin/users", params={"q": "zara"}, headers=headers)
    assert [u["id"] for u in search.json()["items"]] == [str(customer.id)]

    assert (await client.get(f"/api/admin/users/{customer.id}", headers=headers)).status_code == 200

    deactivated = await client.patch(f"/api/admin/users/{customer.id}", json={"is_active": False}, headers=headers)
    assert deactivated.status_code == 200 and deactivated.json()["is_active"] is False
    assert (await client.get("/api/auth/me", headers=auth_headers(customer))).status_code == 403

    self_demote = await client.patch(f"/api/admin/users/{admin.id}", json={"role": "customer"}, headers=headers)
    assert self_demote.status_code == 400


async def test_role_change_blocked_for_users_with_profiles(client, admin, vendor_user, shop):
    response = await client.patch(f"/api/admin/users/{vendor_user.id}", json={"role": "customer"}, headers=auth_headers(admin))
    assert response.status_code == 409


async def test_vendor_and_rider_approval(client, factory, admin):
    pending_vendor = await factory.vendor(status=ApprovalStatus.PENDING, is_open=False)
    pending_rider = await factory.rider(status=ApprovalStatus.PENDING)
    headers = auth_headers(admin)

    vendors = await client.get("/api/admin/vendors", params={"status": "pending"}, headers=headers)
    assert [v["id"] for v in vendors.json()["items"]] == [str(pending_vendor.id)]
    approved = await client.patch(f"/api/admin/vendors/{pending_vendor.id}/approval", json={"status": "approved"}, headers=headers)
    assert approved.json()["status"] == "approved"

    riders = await client.get("/api/admin/riders", params={"status": "pending"}, headers=headers)
    assert [r["id"] for r in riders.json()["items"]] == [str(pending_rider.id)]
    rejected = await client.patch(
        f"/api/admin/riders/{pending_rider.id}/approval", json={"status": "rejected", "reason": "Docs missing"}, headers=headers
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["availability"] == "offline"


async def test_order_monitoring_live_deliveries_and_analytics(client, factory, admin, delivery, rider_user, customer, shop):
    headers = auth_headers(admin)
    await client.post(
        "/api/tracking/location", json={"order_id": delivery["order_id"], "latitude": 12.95, "longitude": 77.6},
        headers=auth_headers(rider_user),
    )

    live = await client.get("/api/admin/deliveries/live", headers=headers)
    assert live.status_code == 200
    [entry] = live.json()
    assert entry["order_id"] == delivery["order_id"]
    assert entry["current_location"]["latitude"] == 12.95
    assert entry["rider"]["name"] == rider_user.name

    # Second order that stays pending + complete the first.
    product = await factory.product(shop, price="10.00")
    await place_order(client, customer, [(product, 1)])
    for status in ("PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED"):
        await set_status(client, rider_user, delivery["order_id"], status)

    orders = await client.get("/api/admin/orders", params={"status": "DELIVERED"}, headers=headers)
    assert orders.json()["total"] == 1
    by_vendor = await client.get("/api/admin/orders", params={"vendor_id": str(shop.id)}, headers=headers)
    assert by_vendor.json()["total"] == 2

    stats = (await client.get("/api/admin/analytics", headers=headers)).json()
    assert stats["total_orders"] == 2
    assert stats["orders_today"] == 2
    assert stats["orders_by_status"] == {"DELIVERED": 1, "ORDER_PLACED": 1}
    assert stats["delivered_revenue"] == 100.0  # 2 x 50.00 from the delivery fixture
    assert stats["average_order_value"] == 100.0
    assert stats["average_delivery_minutes"] is not None
    assert stats["top_vendors"][0]["shop_name"] == shop.shop_name
    assert stats["users_by_role"]["admin"] == 1
    assert stats["active_deliveries"] == 0
    assert stats["riders_by_availability"] == {"online": 1}
