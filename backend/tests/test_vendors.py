from app.models.enums import ApprovalStatus, UserRole
from tests.conftest import auth_headers

SHOP = {"shop_name": "Corner Bakery", "description": "Breads", "address": "1 Brigade Rd, Bengaluru",
        "latitude": 12.97, "longitude": 77.60}


async def test_vendor_crud_and_approval_flow(client, factory, admin):
    owner = await factory.user(UserRole.VENDOR)
    headers = auth_headers(owner)

    created = await client.post("/api/vendors", json=SHOP, headers=headers)
    assert created.status_code == 201, created.text
    vendor = created.json()
    assert vendor["status"] == "pending" and vendor["is_open"] is False

    # Duplicate shop for same vendor is rejected.
    assert (await client.post("/api/vendors", json=SHOP, headers=headers)).status_code == 409

    # Pending shops are hidden from the public but visible to the owner.
    assert (await client.get(f"/api/vendors/{vendor['id']}")).status_code == 404
    assert (await client.get(f"/api/vendors/{vendor['id']}", headers=headers)).status_code == 200
    assert (await client.get("/api/vendors/me", headers=headers)).json()["id"] == vendor["id"]

    # Cannot open before approval.
    opened = await client.patch(f"/api/vendors/{vendor['id']}/availability", json={"is_open": True}, headers=headers)
    assert opened.status_code == 409

    # Vendor cannot approve itself; admin can.
    assert (await client.patch(f"/api/vendors/{vendor['id']}/status", json={"status": "approved"}, headers=headers)).status_code == 403
    approved = await client.patch(
        f"/api/vendors/{vendor['id']}/status", json={"status": "approved"}, headers=auth_headers(admin)
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"

    opened = await client.patch(f"/api/vendors/{vendor['id']}/availability", json={"is_open": True}, headers=headers)
    assert opened.status_code == 200 and opened.json()["is_open"] is True

    updated = await client.put(f"/api/vendors/{vendor['id']}", json={"shop_name": "Corner Bakery & Cafe"}, headers=headers)
    assert updated.status_code == 200 and updated.json()["shop_name"] == "Corner Bakery & Cafe"

    public = await client.get(f"/api/vendors/{vendor['id']}")
    assert public.status_code == 200

    # Owner receives an approval notification.
    notes = (await client.get("/api/notifications", headers=headers)).json()
    assert any(n["type"] == "vendor_status" for n in notes["items"])

    assert (await client.delete(f"/api/vendors/{vendor['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/vendors/{vendor['id']}")).status_code == 404


async def test_vendor_cannot_modify_another_shop(client, factory, shop):
    other = await factory.user(UserRole.VENDOR)
    response = await client.put(f"/api/vendors/{shop.id}", json={"shop_name": "Hijacked"}, headers=auth_headers(other))
    assert response.status_code == 403


async def test_suspending_vendor_closes_shop(client, admin, shop):
    response = await client.patch(
        f"/api/vendors/{shop.id}/status", json={"status": "suspended", "reason": "Hygiene"}, headers=auth_headers(admin)
    )
    assert response.json()["status"] == "suspended" and response.json()["is_open"] is False


async def test_delete_vendor_with_orders_conflicts(client, factory, customer, vendor_user, shop):
    from tests.conftest import place_order

    product = await factory.product(shop)
    await place_order(client, customer, [(product, 1)])
    response = await client.delete(f"/api/vendors/{shop.id}", headers=auth_headers(vendor_user))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VENDOR_HAS_ORDERS"


async def test_list_search_and_nearby(client, factory, admin):
    near = await factory.vendor(shop_name="Near Mart", latitude=12.9716, longitude=77.5946)
    mid = await factory.vendor(shop_name="Mid Mart", latitude=12.99, longitude=77.60)       # ~2.2 km
    await factory.vendor(shop_name="Far Mart", latitude=13.30, longitude=77.60)             # ~36 km
    await factory.vendor(shop_name="Pending Mart", status=ApprovalStatus.PENDING)

    listing = (await client.get("/api/vendors")).json()
    assert listing["total"] == 3  # pending hidden

    search = (await client.get("/api/vendors", params={"q": "near"})).json()
    assert [v["shop_name"] for v in search["items"]] == ["Near Mart"]

    nearby = (await client.get("/api/vendors", params={"latitude": 12.9716, "longitude": 77.5946, "radius_km": 5})).json()
    assert [v["id"] for v in nearby["items"]] == [str(near.id), str(mid.id)]
    assert nearby["items"][0]["distance_km"] == 0
    assert 2 < nearby["items"][1]["distance_km"] < 3

    # Admin can filter by status.
    pending = (await client.get("/api/vendors", params={"status": "pending"}, headers=auth_headers(admin))).json()
    assert [v["shop_name"] for v in pending["items"]] == ["Pending Mart"]

    bad = await client.get("/api/vendors", params={"latitude": 12.9})
    assert bad.status_code == 400
