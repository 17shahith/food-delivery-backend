from app.models.enums import ApprovalStatus, UserRole
from tests.conftest import auth_headers


async def test_product_crud(client, vendor_user, shop):
    headers = auth_headers(vendor_user)
    created = await client.post(
        "/api/products",
        json={"name": "Sourdough", "description": "Fresh loaf", "category": "bakery", "price": "120.50",
              "image_url": "https://cdn.example.com/bread.jpg"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    product = created.json()
    assert product["vendor_id"] == str(shop.id)
    assert product["price"] == 120.5 and product["availability"] is True

    fetched = await client.get(f"/api/products/{product['id']}")
    assert fetched.status_code == 200 and fetched.json()["name"] == "Sourdough"

    updated = await client.put(
        f"/api/products/{product['id']}", json={"price": 99.99, "availability": False}, headers=headers
    )
    assert updated.status_code == 200
    assert updated.json()["price"] == 99.99 and updated.json()["availability"] is False

    assert (await client.delete(f"/api/products/{product['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/products/{product['id']}")).status_code == 404


async def test_product_validation(client, vendor_user, shop):
    response = await client.post(
        "/api/products", json={"name": "", "price": -5, "image_url": "ftp://x"}, headers=auth_headers(vendor_user)
    )
    assert response.status_code == 422


async def test_vendor_without_shop_cannot_add_products(client, factory):
    user = await factory.user(UserRole.VENDOR)
    response = await client.post("/api/products", json={"name": "Milk", "price": 30}, headers=auth_headers(user))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VENDOR_PROFILE_NOT_FOUND"


async def test_vendor_cannot_edit_other_vendors_product(client, factory, shop):
    product = await factory.product(shop)
    other_vendor = await factory.vendor()
    other_user_headers = auth_headers(await _owner(factory, other_vendor))
    response = await client.put(f"/api/products/{product.id}", json={"price": 1}, headers=other_user_headers)
    assert response.status_code == 403
    assert (await client.delete(f"/api/products/{product.id}", headers=other_user_headers)).status_code == 403


async def _owner(factory, vendor):
    from app.models import User

    return await factory.db.get(User, vendor.user_id)


async def test_admin_creates_product_for_vendor(client, admin, shop):
    response = await client.post(
        "/api/products", json={"vendor_id": str(shop.id), "name": "Eggs", "price": 60}, headers=auth_headers(admin)
    )
    assert response.status_code == 201
    missing = await client.post("/api/products", json={"name": "Eggs", "price": 60}, headers=auth_headers(admin))
    assert missing.status_code == 400


async def test_search_filter_and_sort(client, factory, shop, vendor_user):
    await factory.product(shop, name="Apple Juice", price="80.00", category="drinks")
    await factory.product(shop, name="Green Apple", price="40.00", category="fruit")
    await factory.product(shop, name="Banana", price="20.00", category="fruit", availability=False)
    hidden_vendor = await factory.vendor(status=ApprovalStatus.PENDING)
    await factory.product(hidden_vendor, name="Hidden Apple", price="10.00")

    search = (await client.get("/api/products", params={"q": "apple", "sort": "price_asc"})).json()
    assert [p["name"] for p in search["items"]] == ["Green Apple", "Apple Juice"]

    fruit = (await client.get("/api/products", params={"category": "fruit", "available": True})).json()
    assert [p["name"] for p in fruit["items"]] == ["Green Apple"]

    priced = (await client.get("/api/products", params={"min_price": 30, "max_price": 50})).json()
    assert [p["name"] for p in priced["items"]] == ["Green Apple"]

    by_vendor = (await client.get("/api/products", params={"vendor_id": str(shop.id)})).json()
    assert by_vendor["total"] == 3

    page = (await client.get("/api/products", params={"limit": 2, "sort": "name"})).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["items"][0]["name"] == "Apple Juice"

    bad = await client.get("/api/products", params={"min_price": 50, "max_price": 10})
    assert bad.status_code == 400


async def test_unapproved_vendor_products_visible_to_owner_only(client, factory):
    owner = await factory.user(UserRole.VENDOR)
    vendor = await factory.vendor(owner, status=ApprovalStatus.PENDING)
    product = await factory.product(vendor)

    assert (await client.get(f"/api/products/{product.id}")).status_code == 404
    own = await client.get("/api/products", params={"vendor_id": str(vendor.id)}, headers=auth_headers(owner))
    assert own.json()["total"] == 1
    assert (await client.get(f"/api/products/{product.id}", headers=auth_headers(owner))).status_code == 200
