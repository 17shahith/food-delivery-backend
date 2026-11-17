"""End-to-end tests of the React web-app contract under /api/app.

The flows below mirror what the frontend does (see D:/delivery-frontend/frontend/src/services).
"""

import uuid

import pytest
from sqlalchemy import select

from app.models import Order, Product, RiderLocation, User, Vendor
from app.models.enums import ApprovalStatus, OrderStatus, UserRole
from tests.conftest import auth_headers

A = "/api/app"
PASSWORD = "Str0ng-pass!"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def register(client, role: str, email: str, **extra) -> dict:
    body = {"email": email, "password": PASSWORD, "full_name": f"{role.title()} Person", "role": role, **extra}
    response = await client.post(f"{A}/auth/register", json=body)
    assert response.status_code == 201, response.text
    return response.json()


VENDOR_FIELDS = {"store_name": "Fresh Mart", "store_category": "grocery", "store_address": "1 Market Road, Bengaluru",
                 "latitude": 12.9716, "longitude": 77.5946}
ADDRESS = {"label": "Home", "line1": "221B Baker Street", "city": "Bengaluru", "postal_code": "560001",
           "latitude": 12.9352, "longitude": 77.6245}


@pytest.fixture
async def world(client, admin):
    """Registers a customer, an approved open vendor with products, and an approved online rider via the app API."""
    customer = await register(client, "customer", "cust@example.com", phone="+919800000001")
    vendor = await register(client, "vendor", "shop@example.com", **VENDOR_FIELDS)
    rider = await register(client, "rider", "ride@example.com", vehicle_type="motorbike", vehicle_number="KA01")
    ch, vh, rh = bearer(customer["access_token"]), bearer(vendor["access_token"]), bearer(rider["access_token"])
    ah = auth_headers(admin)

    assert vendor["user"]["approval_status"] == "pending"
    assert rider["user"]["approval_status"] == "pending"
    assert customer["user"]["approval_status"] is None

    store = (await client.get(f"{A}/vendors/me", headers=vh)).json()
    assert store["name"] == "Fresh Mart" and store["category"] == "grocery"
    rider_profile = (await client.get(f"{A}/riders/me", headers=rh)).json()
    assert rider_profile["vehicle_type"] == "motorbike" and rider_profile["is_online"] is False

    assert (await client.post(f"{A}/admin/vendors/{store['id']}/approve", json={}, headers=ah)).status_code == 200
    assert (await client.post(f"{A}/admin/riders/{rider_profile['id']}/approve", json={}, headers=ah)).status_code == 200
    me = (await client.get(f"{A}/auth/me", headers=vh)).json()
    assert me["approval_status"] == "approved"

    patched = await client.patch(f"{A}/vendors/me", json={"is_open": True, "delivery_fee": 25, "phone": "+919811111111"}, headers=vh)
    assert patched.status_code == 200, patched.text
    assert patched.json()["is_open"] is True and patched.json()["delivery_fee"] == 25

    products = []
    for name, price in (("Milk", 30), ("Bread", 45.5)):
        r = await client.post(f"{A}/products", json={"name": name, "price": price, "category": "dairy", "is_available": True,
                                                      "stock": 10, "unit": "pack"}, headers=vh)
        assert r.status_code == 201, r.text
        products.append(r.json())

    online = await client.patch(f"{A}/riders/me/availability", json={"is_online": True}, headers=rh)
    assert online.status_code == 200 and online.json()["is_online"] is True
    await client.post(f"{A}/tracking/location", json={"latitude": 12.97, "longitude": 77.59}, headers=rh)

    address = (await client.post(f"{A}/customers/me/addresses", json=ADDRESS, headers=ch)).json()
    assert address["is_default"] is True

    return {"ch": ch, "vh": vh, "rh": rh, "ah": ah, "store": store, "products": products, "address": address,
            "rider": rider_profile, "customer": customer["user"], "vendor_user": vendor["user"]}


async def place(client, w, qty=2) -> dict:
    body = {"vendor_id": w["store"]["id"], "address_id": w["address"]["id"], "payment_method": "cod",
            "items": [{"product_id": w["products"][0]["id"], "quantity": qty}, {"product_id": w["products"][1]["id"], "quantity": 1}],
            "notes": "Ring the bell"}
    response = await client.post(f"{A}/orders", json=body, headers=w["ch"])
    assert response.status_code == 201, response.text
    return response.json()


async def test_full_delivery_flow(client, world, db):
    w = world
    order = await place(client, w)
    assert order["status"] == "placed"
    assert order["subtotal"] == 105.5 and order["delivery_fee"] == 25 and order["total"] == 130.5
    assert order["payment_method"] == "cod"
    assert order["delivery_address"]["line1"] == "221B Baker Street" and order["delivery_address"]["city"] == "Bengaluru"
    assert order["vendor"]["name"] == "Fresh Mart" and order["customer"]["full_name"] == "Customer Person"
    assert order["rider_earning"] > 30 and order["distance_km"] > 0
    assert [h["status"] for h in order["status_history"]] == ["placed"]
    assert {i["product_name"]: i["subtotal"] for i in order["items"]} == {"Milk": 60.0, "Bread": 45.5}
    oid = order["id"]

    # Stock was reserved.
    milk = await db.get(Product, uuid.UUID(w["products"][0]["id"]))
    await db.refresh(milk)
    assert milk.stock == 8

    # Vendor sees and works the order.
    incoming = (await client.get(f"{A}/vendors/me/orders", params={"status": "placed"}, headers=w["vh"])).json()
    assert [o["id"] for o in incoming] == [oid]
    stats = (await client.get(f"{A}/vendors/me/stats", headers=w["vh"])).json()
    assert stats["pending_orders"] == 1 and stats["orders_today"] == 1 and stats["total_products"] == 2
    assert (await client.post(f"{A}/orders/{oid}/accept", headers=w["vh"])).json()["status"] == "accepted"
    assert (await client.patch(f"{A}/orders/{oid}/status", json={"status": "preparing"}, headers=w["vh"])).json()["status"] == "preparing"
    ready = await client.patch(f"{A}/orders/{oid}/status", json={"status": "ready_for_pickup"}, headers=w["vh"])
    assert ready.json()["status"] == "ready_for_pickup"

    # Rider job board -> preview -> accept.
    available = (await client.get(f"{A}/deliveries/available", headers=w["rh"])).json()
    assert [o["id"] for o in available] == [oid]
    assert (await client.get(f"{A}/orders/{oid}", headers=w["rh"])).status_code == 200  # preview before accepting
    taken = await client.post(f"{A}/deliveries/{oid}/accept", headers=w["rh"])
    assert taken.status_code == 200, taken.text
    assert taken.json()["status"] == "rider_assigned"
    assert taken.json()["rider"]["full_name"] == "Rider Person"
    assert taken.json()["rider_id"] == w["rider"]["id"]
    assert (await client.get(f"{A}/deliveries/available", headers=w["rh"])).json() == []

    # Live GPS -> rider_locations row + customer tracking.
    sent = await client.post(f"{A}/tracking/location", json={"order_id": oid, "latitude": 12.96, "longitude": 77.60, "heading": 45},
                             headers=w["rh"])
    assert sent.status_code == 200, sent.text
    loc = (await client.get(f"{A}/tracking/orders/{oid}", headers=w["ch"])).json()
    assert loc["latitude"] == 12.96 and loc["rider_id"] == w["rider"]["id"] and loc["rider_name"] == "Rider Person"
    row = (await db.execute(select(RiderLocation).execution_options(populate_existing=True))).scalar_one()
    assert str(row.order_id) == oid and row.latitude == 12.96
    active = (await client.get(f"{A}/tracking/active", headers=w["ah"])).json()
    assert [a["rider_id"] for a in active] == [w["rider"]["id"]]

    for step in ("picked_up", "out_for_delivery", "delivered"):
        r = await client.patch(f"{A}/deliveries/{oid}/status", json={"status": step}, headers=w["rh"])
        assert r.status_code == 200, r.text
    final = (await client.get(f"{A}/orders/{oid}", headers=w["ch"])).json()
    assert final["status"] == "delivered"
    assert [h["status"] for h in final["status_history"]] == [
        "placed", "accepted", "preparing", "ready_for_pickup", "rider_assigned", "picked_up", "out_for_delivery", "delivered"]

    # Customer no longer sees the rider after delivery.
    assert (await client.get(f"{A}/tracking/orders/{oid}", headers=w["ch"])).json() is None

    rider_stats = (await client.get(f"{A}/riders/me/stats", headers=w["rh"])).json()
    assert rider_stats["deliveries_today"] == 1 and rider_stats["total_deliveries"] == 1
    assert rider_stats["earnings_today"] == final["rider_earning"]
    history = (await client.get(f"{A}/riders/me/deliveries", params={"status": "delivered"}, headers=w["rh"])).json()
    assert [o["id"] for o in history] == [oid]
    assert (await client.get(f"{A}/riders/me", headers=w["rh"])).json()["total_deliveries"] == 1

    mine = (await client.get(f"{A}/orders/me", params={"status": "delivered"}, headers=w["ch"])).json()
    assert [o["id"] for o in mine] == [oid]

    notes = (await client.get(f"{A}/notifications", headers=w["ch"])).json()
    assert {"order", "delivery"} <= {n["type"] for n in notes}

    stats = (await client.get(f"{A}/admin/stats", headers=w["ah"])).json()
    assert stats["total_customers"] == 1 and stats["revenue_today"] == 130.5 and stats["online_riders"] == 1
    analytics = (await client.get(f"{A}/admin/analytics", params={"days": 7}, headers=w["ah"])).json()
    assert len(analytics["daily"]) == 7 and analytics["daily"][-1]["revenue"] == 130.5
    assert analytics["status_breakdown"] == [{"status": "delivered", "count": 1}]
    assert analytics["top_vendors"][0]["name"] == "Fresh Mart"
    admin_orders = (await client.get(f"{A}/admin/orders", params={"search": "fresh"}, headers=w["ah"])).json()
    assert [o["id"] for o in admin_orders] == [oid]


async def test_vendor_reject_restores_stock_and_notifies(client, world, db):
    w = world
    oid = (await place(client, w, qty=3))["id"]
    rejected = await client.post(f"{A}/orders/{oid}/reject", json={"reason": "Out of milk"}, headers=w["vh"])
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected" and rejected.json()["rejection_reason"] == "Out of milk"
    assert rejected.json()["status_history"][-1]["status"] == "rejected"
    milk = (await db.execute(select(Product).where(Product.id == uuid.UUID(w["products"][0]["id"]))
                             .execution_options(populate_existing=True))).scalar_one()
    assert milk.stock == 10
    notes = (await client.get(f"{A}/notifications", headers=w["ch"])).json()
    assert any(n["title"] == "Order rejected" for n in notes)
    # Terminal: cannot accept afterwards.
    assert (await client.post(f"{A}/orders/{oid}/accept", headers=w["vh"])).status_code == 409


async def test_customer_cancel_and_errors_are_readable(client, world):
    w = world
    oid = (await place(client, w))["id"]
    cancelled = await client.post(f"{A}/orders/{oid}/cancel", json={"reason": "Changed my mind"}, headers=w["ch"])
    assert cancelled.json()["status"] == "cancelled"
    again = await client.post(f"{A}/orders/{oid}/cancel", json={}, headers=w["ch"])
    assert again.status_code == 409
    assert isinstance(again.json()["detail"], str) and again.json()["detail"]  # FastAPI-style message for the UI

    invalid = await client.post(f"{A}/orders", json={"vendor_id": "x"}, headers=w["ch"])
    assert invalid.status_code == 422
    assert isinstance(invalid.json()["detail"], list) and invalid.json()["detail"][0]["loc"]


async def test_checkout_rules(client, world, factory):
    w = world
    base = {"vendor_id": w["store"]["id"], "address_id": w["address"]["id"], "payment_method": "online"}

    other_shop = await factory.vendor()
    foreign = await factory.product(other_shop)
    mixed = await client.post(f"{A}/orders", json=base | {"items": [{"product_id": str(foreign.id), "quantity": 1}]}, headers=w["ch"])
    assert mixed.status_code == 400 and mixed.json()["error"]["code"] == "MIXED_VENDOR_CART"

    bad_address = await client.post(f"{A}/orders", json=base | {"address_id": str(uuid.uuid4()),
                                     "items": [{"product_id": w["products"][0]["id"], "quantity": 1}]}, headers=w["ch"])
    assert bad_address.status_code == 404

    too_many = await client.post(f"{A}/orders", json=base | {"items": [{"product_id": w["products"][0]["id"], "quantity": 11}]},
                                 headers=w["ch"])
    assert too_many.status_code == 409 and "only 10 left" in too_many.json()["detail"]

    await client.patch(f"{A}/vendors/me", json={"min_order_amount": 500}, headers=w["vh"])
    below_min = await client.post(f"{A}/orders", json=base | {"items": [{"product_id": w["products"][0]["id"], "quantity": 1}]},
                                  headers=w["ch"])
    assert below_min.status_code == 409 and below_min.json()["error"]["code"] == "MIN_ORDER_NOT_MET"

    await client.patch(f"{A}/vendors/me", json={"min_order_amount": 0, "is_open": False}, headers=w["vh"])
    closed = await client.post(f"{A}/orders", json=base | {"items": [{"product_id": w["products"][0]["id"], "quantity": 1}]},
                               headers=w["ch"])
    assert closed.status_code == 409 and "closed" in closed.json()["detail"]


async def test_rider_skip_and_conflicts(client, world, factory):
    w = world
    oid = (await place(client, w))["id"]
    await client.post(f"{A}/orders/{oid}/accept", headers=w["vh"])
    for s in ("preparing", "ready_for_pickup"):
        await client.patch(f"{A}/orders/{oid}/status", json={"status": s}, headers=w["vh"])

    skip = await client.post(f"{A}/deliveries/{oid}/reject", json={"reason": "Too far"}, headers=w["rh"])
    assert skip.status_code == 204
    assert (await client.get(f"{A}/deliveries/available", headers=w["rh"])).json() == []

    # A second rider takes it; the first can no longer.
    second_user = await factory.user(UserRole.RIDER)
    await factory.rider(second_user)
    assert len((await client.get(f"{A}/deliveries/available", headers=auth_headers(second_user))).json()) == 1
    assert (await client.post(f"{A}/deliveries/{oid}/accept", headers=auth_headers(second_user))).status_code == 200
    late = await client.post(f"{A}/deliveries/{oid}/accept", headers=w["rh"])
    assert late.status_code == 409 and late.json()["detail"] == "This delivery was already taken by another rider"

    # Offline riders see nothing; on-delivery riders stay "online".
    assert (await client.patch(f"{A}/riders/me/availability", json={"is_online": False}, headers=w["rh"])).json()["is_online"] is False
    assert (await client.get(f"{A}/deliveries/available", headers=w["rh"])).json() == []
    busy = await client.patch(f"{A}/riders/me/availability", json={"is_online": True}, headers=auth_headers(second_user))
    assert busy.status_code == 200 and busy.json()["is_online"] is True
    # Non-assigned rider cannot advance the delivery.
    assert (await client.patch(f"{A}/deliveries/{oid}/status", json={"status": "picked_up"}, headers=w["rh"])).status_code == 404


async def test_unapproved_rider_and_vendor_are_limited(client, admin):
    rider = await register(client, "rider", "new-rider@example.com", vehicle_type="scooter")
    rh = bearer(rider["access_token"])
    online = await client.patch(f"{A}/riders/me/availability", json={"is_online": True}, headers=rh)
    assert online.status_code == 409
    assert (await client.get(f"{A}/deliveries/available", headers=rh)).json() == []

    vendor = await register(client, "vendor", "new-shop@example.com", **VENDOR_FIELDS)
    opened = await client.patch(f"{A}/vendors/me", json={"is_open": True}, headers=bearer(vendor["access_token"]))
    assert opened.status_code == 409
    assert (await client.get(f"{A}/vendors")).json() == []  # pending shops are hidden


async def test_register_validation(client):
    missing_store = await client.post(f"{A}/auth/register", json={"email": "v@example.com", "password": PASSWORD,
                                                                  "full_name": "Vee", "role": "vendor"})
    assert missing_store.status_code == 422
    admin_role = await client.post(f"{A}/auth/register", json={"email": "a@example.com", "password": PASSWORD,
                                                               "full_name": "Ad", "role": "admin"})
    assert admin_role.status_code == 422


async def test_auth_session_profile_and_passwords(client, fake_auth):
    reg = await register(client, "customer", "pat@example.com")
    assert set(reg) >= {"access_token", "refresh_token", "user"}
    h = bearer(reg["access_token"])

    login = await client.post(f"{A}/auth/login", json={"email": "pat@example.com", "password": PASSWORD})
    assert login.status_code == 200 and login.json()["user"]["full_name"] == "Customer Person"
    bad = await client.post(f"{A}/auth/login", json={"email": "pat@example.com", "password": "nope"})
    assert bad.status_code == 401 and bad.json()["detail"] == "Invalid email or password"

    refreshed = (await client.post(f"{A}/auth/refresh", json={"refresh_token": reg["refresh_token"]})).json()
    assert set(refreshed) == {"access_token", "refresh_token"}

    me = await client.patch(f"{A}/auth/me", json={"full_name": "Pat Doe", "phone": "+919822222222",
                                                  "avatar_url": "https://cdn.example.com/a.png"}, headers=h)
    assert me.status_code == 200
    assert me.json()["full_name"] == "Pat Doe" and me.json()["avatar_url"] == "https://cdn.example.com/a.png"

    wrong = await client.post(f"{A}/auth/change-password", json={"current_password": "bad", "new_password": "N3w-password!"}, headers=h)
    assert wrong.status_code == 400 and wrong.json()["detail"] == "Current password is incorrect"
    ok = await client.post(f"{A}/auth/change-password", json={"current_password": PASSWORD, "new_password": "N3w-password!"}, headers=h)
    assert ok.status_code == 200
    assert (await client.post(f"{A}/auth/login", json={"email": "pat@example.com", "password": "N3w-password!"})).status_code == 200

    forgot = await client.post(f"{A}/auth/forgot-password",
                               json={"email": "pat@example.com", "redirect_to": "http://localhost:5173/reset-password"})
    assert forgot.status_code == 200
    evil = await client.post(f"{A}/auth/forgot-password", json={"email": "pat@example.com", "redirect_to": "https://evil.example/x"})
    assert evil.status_code == 200
    assert fake_auth.recovery_requests == [
        ("pat@example.com", "http://localhost:5173/reset-password"), ("pat@example.com", None)]

    assert (await client.post(f"{A}/auth/logout", headers=h)).status_code == 204

    fake_auth.require_confirmation = True
    pending = await client.post(f"{A}/auth/register", json={"email": "conf@example.com", "password": PASSWORD,
                                                            "full_name": "Con Firm", "role": "customer"})
    assert pending.status_code == 201 and "message" in pending.json()


async def test_address_book(client, world, factory):
    w = world
    second = (await client.post(f"{A}/customers/me/addresses", json=ADDRESS | {"label": "Work", "is_default": True},
                                headers=w["ch"])).json()
    addresses = (await client.get(f"{A}/customers/me/addresses", headers=w["ch"])).json()
    assert [a["label"] for a in addresses] == ["Work", "Home"] and [a["is_default"] for a in addresses] == [True, False]

    patched = await client.patch(f"{A}/customers/me/addresses/{w['address']['id']}", json={"line2": "Flat 4", "is_default": True},
                                 headers=w["ch"])
    assert patched.json()["is_default"] is True and patched.json()["line2"] == "Flat 4"

    assert (await client.delete(f"{A}/customers/me/addresses/{w['address']['id']}", headers=w["ch"])).status_code == 204
    remaining = (await client.get(f"{A}/customers/me/addresses", headers=w["ch"])).json()
    assert [(a["id"], a["is_default"]) for a in remaining] == [(second["id"], True)]

    stranger = await factory.user(UserRole.CUSTOMER)
    assert (await client.patch(f"{A}/customers/me/addresses/{second['id']}", json={"label": "x"},
                               headers=auth_headers(stranger))).status_code == 404
    assert (await client.get(f"{A}/customers/me/addresses", headers=w["vh"])).status_code == 403


async def test_catalog_browse_and_product_management(client, world, factory):
    w = world
    far = await factory.vendor(shop_name="Far Away Foods", latitude=13.5, longitude=77.6)
    far.category = "restaurant"
    await factory.db.commit()

    near = (await client.get(f"{A}/vendors", params={"latitude": 12.9716, "longitude": 77.5946})).json()
    assert [v["name"] for v in near] == ["Fresh Mart", "Far Away Foods"] and near[0]["distance_km"] == 0
    in_radius = (await client.get(f"{A}/vendors", params={"latitude": 12.9716, "longitude": 77.5946, "radius_km": 5})).json()
    assert [v["name"] for v in in_radius] == ["Fresh Mart"]
    assert [v["name"] for v in (await client.get(f"{A}/vendors", params={"category": "restaurant"})).json()] == ["Far Away Foods"]
    assert [v["name"] for v in (await client.get(f"{A}/vendors", params={"search": "fresh m"})).json()] == ["Fresh Mart"]
    assert (await client.get(f"{A}/vendors/{w['store']['id']}")).json()["approval_status"] == "approved"

    products = (await client.get(f"{A}/products", params={"search": "bread"})).json()
    assert [p["name"] for p in products] == ["Bread"]
    assert products[0]["vendor"] == {"id": w["store"]["id"], "name": "Fresh Mart", "is_open": True, "delivery_fee": 25.0}
    pid = products[0]["id"]

    off = await client.patch(f"{A}/products/{pid}/availability", json={"is_available": False}, headers=w["vh"])
    assert off.json()["is_available"] is False
    assert [p["name"] for p in (await client.get(f"{A}/products", params={"available": "true"})).json()] == ["Milk"]
    assert len((await client.get(f"{A}/vendors/me/products", headers=w["vh"])).json()) == 2

    edited = await client.patch(f"{A}/products/{pid}", json={"price": 50, "image_url": "", "unit": "loaf"}, headers=w["vh"])
    assert edited.json()["price"] == 50 and edited.json()["unit"] == "loaf"
    assert (await client.get(f"{A}/products/{pid}")).json()["vendor"]["name"] == "Fresh Mart"

    other = await factory.user(UserRole.VENDOR)
    await factory.vendor(other)
    assert (await client.patch(f"{A}/products/{pid}", json={"price": 1}, headers=auth_headers(other))).status_code == 403
    assert (await client.delete(f"{A}/products/{pid}", headers=w["vh"])).status_code == 204
    assert (await client.get(f"{A}/products/{pid}")).status_code == 404


async def test_image_upload(client, world, storage):
    w = world
    ok = await client.post(f"{A}/products/upload-image", files={"file": ("p.png", PNG, "image/png")}, headers=w["vh"])
    assert ok.status_code == 200 and ok.json()["url"].startswith("https://")
    assert storage.uploads and str(storage.uploads[0][0]) == w["store"]["id"]

    fake = await client.post(f"{A}/products/upload-image", files={"file": ("x.png", b"<script>", "image/png")}, headers=w["vh"])
    assert fake.status_code == 400
    assert (await client.post(f"{A}/products/upload-image", files={"file": ("p.png", PNG, "image/png")},
                              headers=w["ch"])).status_code == 403


async def test_admin_management(client, world):
    w = world
    customers = (await client.get(f"{A}/admin/users", params={"role": "customer"}, headers=w["ah"])).json()
    assert [u["email"] for u in customers] == ["cust@example.com"]
    vendors = (await client.get(f"{A}/admin/users", params={"role": "vendor", "search": "shop@"}, headers=w["ah"])).json()
    assert vendors[0]["approval_status"] == "approved"

    off = await client.patch(f"{A}/admin/users/{w['customer']['id']}/status", json={"is_active": False}, headers=w["ah"])
    assert off.json()["is_active"] is False
    assert (await client.get(f"{A}/auth/me", headers=w["ch"])).status_code == 403
    inactive = (await client.get(f"{A}/admin/users", params={"status": "inactive"}, headers=w["ah"])).json()
    assert [u["id"] for u in inactive] == [w["customer"]["id"]]

    shops = (await client.get(f"{A}/admin/vendors", params={"status": "approved"}, headers=w["ah"])).json()
    assert shops[0]["owner"]["email"] == "shop@example.com"
    riders = (await client.get(f"{A}/admin/riders", params={"search": "KA01"}, headers=w["ah"])).json()
    assert riders[0]["user"]["full_name"] == "Rider Person"

    rejected = await client.post(f"{A}/admin/riders/{w['rider']['id']}/reject", json={"reason": "Docs"}, headers=w["ah"])
    assert rejected.json()["approval_status"] == "rejected" and rejected.json()["is_online"] is False

    assert (await client.get(f"{A}/admin/stats", headers=w["vh"])).status_code == 403


async def test_notifications_inbox(client, world):
    w = world
    await place(client, w)
    inbox = (await client.get(f"{A}/notifications", headers=w["vh"])).json()
    assert inbox and all(n["type"] in {"order", "delivery", "system", "approval", "promo"} for n in inbox)
    first = inbox[0]["id"]
    assert (await client.patch(f"{A}/notifications/{first}/read", headers=w["vh"])).json()["is_read"] is True
    assert (await client.post(f"{A}/notifications/read-all", headers=w["vh"])).status_code == 200
    assert all(n["is_read"] for n in (await client.get(f"{A}/notifications", headers=w["vh"])).json())
    assert (await client.delete(f"{A}/notifications/{first}", headers=w["vh"])).status_code == 204
    assert first not in {n["id"] for n in (await client.get(f"{A}/notifications", headers=w["vh"])).json()}


async def test_order_privacy(client, world, factory):
    w = world
    oid = (await place(client, w))["id"]
    stranger = await factory.user(UserRole.CUSTOMER)
    assert (await client.get(f"{A}/orders/{oid}", headers=auth_headers(stranger))).status_code == 404
    assert (await client.get(f"{A}/tracking/orders/{oid}", headers=auth_headers(stranger))).status_code == 404
    # Riders can't preview orders that are not ready for pickup yet.
    assert (await client.get(f"{A}/orders/{oid}", headers=w["rh"])).status_code == 404
