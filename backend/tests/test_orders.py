import pytest
from sqlalchemy import func, select

from app.models import Checkout, Order
from app.models.enums import UserRole
from tests.conftest import advance_to_ready, auth_headers, place_order, set_status


async def test_create_single_vendor_order(client, factory, customer, shop):
    bread = await factory.product(shop, price="45.50")
    milk = await factory.product(shop, price="30.00")
    checkout = await place_order(client, customer, [(bread, 2), (milk, 1), (bread, 1)])  # duplicates are merged

    assert checkout["checkout_reference"].startswith("CHK-")
    assert checkout["total_amount"] == 166.5
    [order] = checkout["orders"]
    assert order["order_number"].startswith("ORD-")
    assert order["order_status"] == "ORDER_PLACED"
    assert order["vendor_name"] == shop.shop_name
    quantities = {i["product_name"]: i["quantity"] for i in order["items"]}
    assert quantities == {bread.name: 3, milk.name: 1}

    detail = await client.get(f"/api/orders/{order['id']}", headers=auth_headers(customer))
    assert detail.status_code == 200
    assert [t["status"] for t in detail.json()["timeline"]] == ["ORDER_PLACED"]
    assert detail.json()["timeline"][0]["updated_by"] == str(customer.id)


async def test_multi_vendor_checkout_splits_orders(client, factory, customer, db):
    shop_a = await factory.vendor(shop_name="A Mart")
    shop_b = await factory.vendor(shop_name="B Mart")
    a1 = await factory.product(shop_a, price="10.00")
    b1 = await factory.product(shop_b, price="25.00")
    b2 = await factory.product(shop_b, price="5.00")

    checkout = await place_order(client, customer, [(a1, 3), (b1, 1), (b2, 2)])
    assert len(checkout["orders"]) == 2
    assert checkout["total_amount"] == 65.0
    by_vendor = {o["vendor_id"]: o for o in checkout["orders"]}
    assert by_vendor[str(shop_a.id)]["total_amount"] == 30.0
    assert by_vendor[str(shop_b.id)]["total_amount"] == 35.0
    assert {o["checkout_reference"] for o in checkout["orders"]} == {checkout["checkout_reference"]}
    assert len({o["order_number"] for o in checkout["orders"]}) == 2

    listing = await client.get(
        "/api/orders", params={"checkout_reference": checkout["checkout_reference"]}, headers=auth_headers(customer)
    )
    assert listing.json()["total"] == 2


async def test_idempotency_key_prevents_duplicate_orders(client, factory, customer, db):
    product = await factory.product(await factory.vendor())
    headers = auth_headers(customer) | {"Idempotency-Key": "cart-123"}
    body = {"items": [{"product_id": str(product.id), "quantity": 1}], "delivery_address": "Somewhere 123"}

    first = await client.post("/api/orders", json=body, headers=headers)
    second = await client.post("/api/orders", json=body, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()["checkout_reference"] == second.json()["checkout_reference"]
    assert (await db.scalar(select(func.count()).select_from(Order))) == 1
    assert (await db.scalar(select(func.count()).select_from(Checkout))) == 1


async def test_unavailable_products_rejected_atomically(client, factory, customer, db):
    open_shop = await factory.vendor()
    closed_shop = await factory.vendor(is_open=False)
    ok = await factory.product(open_shop)
    off = await factory.product(open_shop, availability=False)
    closed = await factory.product(closed_shop)

    response = await client.post(
        "/api/orders",
        json={"items": [{"product_id": str(p.id), "quantity": 1} for p in (ok, off, closed)], "delivery_address": "Addr 123"},
        headers=auth_headers(customer),
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "PRODUCT_UNAVAILABLE"
    assert {d["product_id"] for d in error["details"]} == {str(off.id), str(closed.id)}
    # Nothing was written (transaction rolled back / never committed).
    assert (await db.scalar(select(func.count()).select_from(Order))) == 0


async def test_unknown_product_returns_404(client, customer):
    import uuid

    response = await client.post(
        "/api/orders",
        json={"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}], "delivery_address": "Addr 123"},
        headers=auth_headers(customer),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


async def test_order_validation(client, customer):
    response = await client.post(
        "/api/orders", json={"items": [], "delivery_address": "x"}, headers=auth_headers(customer)
    )
    assert response.status_code == 422


async def test_order_visibility_is_scoped(client, factory, customer, vendor_user, shop):
    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]

    other_customer = await factory.user(UserRole.CUSTOMER)
    other_vendor_user = await factory.user(UserRole.VENDOR)
    await factory.vendor(other_vendor_user)

    assert (await client.get(f"/api/orders/{order_id}", headers=auth_headers(other_customer))).status_code == 404
    assert (await client.get(f"/api/orders/{order_id}", headers=auth_headers(other_vendor_user))).status_code == 404
    assert (await client.get(f"/api/orders/{order_id}", headers=auth_headers(vendor_user))).status_code == 200
    assert (await client.get("/api/orders", headers=auth_headers(other_customer))).json()["total"] == 0
    assert (await client.get("/api/orders", headers=auth_headers(vendor_user))).json()["total"] == 1


async def test_full_status_progression_and_timeline(client, delivery, customer, rider_user, vendor_user):
    order_id = delivery["order_id"]
    for status in ("PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED"):
        response = await set_status(client, rider_user, order_id, status)
        assert response.status_code == 200, response.text
        assert response.json()["order_status"] == status

    detail = (await client.get(f"/api/orders/{order_id}", headers=auth_headers(customer))).json()
    assert [t["status"] for t in detail["timeline"]] == [
        "ORDER_PLACED", "VENDOR_ACCEPTED", "PREPARING", "READY_FOR_PICKUP",
        "RIDER_ASSIGNED", "PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED",
    ]
    assert detail["timeline"][1]["updated_by"] == str(vendor_user.id)
    assert detail["timeline"][-1]["updated_by"] == str(rider_user.id)
    assert detail["rider"]["name"] == rider_user.name


@pytest.mark.parametrize("target", ["PREPARING", "READY_FOR_PICKUP", "PICKED_UP", "DELIVERED", "ORDER_PLACED"])
async def test_invalid_transitions_rejected(client, factory, customer, vendor_user, shop, admin, target):
    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    response = await set_status(client, admin, order_id, target)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"
    assert response.json()["error"]["details"]["allowed"] == ["CANCELLED", "REJECTED", "VENDOR_ACCEPTED"]


async def test_status_actor_rules(client, factory, customer, vendor_user, shop):
    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]

    # Customer cannot accept their own order.
    assert (await set_status(client, customer, order_id, "VENDOR_ACCEPTED")).status_code == 403
    # Another vendor cannot touch it (cannot even see it).
    other = await factory.user(UserRole.VENDOR)
    await factory.vendor(other)
    assert (await set_status(client, other, order_id, "VENDOR_ACCEPTED")).status_code == 404
    # RIDER_ASSIGNED is only reachable through the assignment flow.
    await advance_to_ready(client, vendor_user, order_id)
    response = await set_status(client, vendor_user, order_id, "RIDER_ASSIGNED")
    assert response.status_code == 400 and response.json()["error"]["code"] == "USE_ASSIGNMENT_FLOW"
    # Skipping steps is an invalid transition.
    assert (await set_status(client, vendor_user, order_id, "PICKED_UP")).status_code == 409


async def test_only_assigned_rider_drives_delivery_steps(client, delivery, factory, vendor_user, customer):
    order_id = delivery["order_id"]
    # Valid transition, wrong actor.
    assert (await set_status(client, vendor_user, order_id, "PICKED_UP")).status_code == 403
    assert (await set_status(client, customer, order_id, "PICKED_UP")).status_code == 403
    other_rider = await factory.user(UserRole.RIDER)
    await factory.rider(other_rider)
    assert (await set_status(client, other_rider, order_id, "PICKED_UP")).status_code == 404


async def test_customer_cancellation_rules(client, factory, customer, vendor_user, shop):
    product = await factory.product(shop)
    first = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    second = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]

    cancelled = await client.post(f"/api/orders/{first}/cancel", json={"reason": "Changed mind"}, headers=auth_headers(customer))
    assert cancelled.status_code == 200
    assert cancelled.json()["order_status"] == "CANCELLED"
    assert cancelled.json()["cancellation_reason"] == "Changed mind"

    again = await client.post(f"/api/orders/{first}/cancel", headers=auth_headers(customer))
    assert again.status_code == 409

    await set_status(client, vendor_user, second, "VENDOR_ACCEPTED")
    late = await client.post(f"/api/orders/{second}/cancel", headers=auth_headers(customer))
    assert late.status_code == 409

    by_vendor = await client.post(f"/api/orders/{second}/cancel", json={"reason": "Out of stock"}, headers=auth_headers(vendor_user))
    assert by_vendor.status_code == 200


async def test_cancel_after_rider_assignment_releases_rider(client, delivery, vendor_user, rider, db):
    from app.models import Rider
    from app.models.enums import RiderAvailability

    response = await client.post(f"/api/orders/{delivery['order_id']}/cancel", headers=auth_headers(vendor_user))
    assert response.status_code == 200
    refreshed = (await db.execute(select(Rider).where(Rider.id == rider.id).execution_options(populate_existing=True))).scalar_one()
    assert refreshed.availability == RiderAvailability.ONLINE


async def test_cannot_cancel_after_pickup(client, delivery, rider_user, admin):
    await set_status(client, rider_user, delivery["order_id"], "PICKED_UP")
    response = await client.post(f"/api/orders/{delivery['order_id']}/cancel", headers=auth_headers(admin))
    assert response.status_code == 409
