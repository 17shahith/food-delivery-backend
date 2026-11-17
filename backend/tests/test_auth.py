import uuid

from sqlalchemy import select

from app.models import Rider, User, Vendor
from app.models.enums import ApprovalStatus, UserRole
from tests.conftest import auth_headers, make_token

PASSWORD = "S3cure-pass!"


async def test_register_customer_creates_profile_and_session(client, db, fake_auth):
    response = await client.post(
        "/api/auth/register/customer",
        json={"name": "Asha", "email": "Asha@Example.com", "password": PASSWORD, "phone": "+919876543210"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["role"] == "customer"
    assert body["user"]["email"] == "asha@example.com"
    assert body["session"]["access_token"]
    assert body["email_confirmation_required"] is False
    assert fake_auth.accounts["asha@example.com"]["metadata"]["role"] == "customer"

    user = await db.get(User, uuid.UUID(body["user"]["id"]))
    assert user is not None and user.role == UserRole.CUSTOMER


async def test_register_vendor_with_shop_is_pending_and_admins_notified(client, db, admin):
    response = await client.post(
        "/api/auth/register/vendor",
        json={
            "name": "Ravi", "email": "ravi@example.com", "password": PASSWORD,
            "shop": {"shop_name": "Ravi Fresh", "address": "5 Market St, Pune", "latitude": 18.52, "longitude": 73.85},
        },
    )
    assert response.status_code == 201, response.text
    user_id = uuid.UUID(response.json()["user"]["id"])
    vendor = (await db.execute(select(Vendor).where(Vendor.user_id == user_id))).scalar_one()
    assert vendor.status == ApprovalStatus.PENDING and vendor.is_open is False

    notes = await client.get("/api/notifications", headers=auth_headers(admin))
    assert any(n["type"] == "vendor_pending" for n in notes.json()["items"])


async def test_register_rider_creates_pending_profile(client, db):
    response = await client.post(
        "/api/auth/register/rider",
        json={"name": "Kiran", "email": "kiran@example.com", "password": PASSWORD, "vehicle_type": "scooter"},
    )
    assert response.status_code == 201, response.text
    rider = (await db.execute(select(Rider).where(Rider.user_id == uuid.UUID(response.json()["user"]["id"])))).scalar_one()
    assert rider.status == ApprovalStatus.PENDING


async def test_register_duplicate_email_conflicts(client):
    payload = {"name": "Dup", "email": "dup@example.com", "password": PASSWORD}
    assert (await client.post("/api/auth/register/customer", json=payload)).status_code == 201
    response = await client.post("/api/auth/register/customer", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_EXISTS"


async def test_register_validation_errors(client):
    response = await client.post(
        "/api/auth/register/customer", json={"name": "A", "email": "not-an-email", "password": "short"}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert {tuple(d["loc"])[-1] for d in error["details"]} >= {"name", "email", "password"}


async def test_register_with_email_confirmation_returns_no_session(client, fake_auth):
    fake_auth.require_confirmation = True
    response = await client.post(
        "/api/auth/register/customer", json={"name": "Conf", "email": "conf@example.com", "password": PASSWORD}
    )
    assert response.status_code == 201
    assert response.json()["session"] is None
    assert response.json()["email_confirmation_required"] is True


async def test_login_logout_and_me(client, fake_auth):
    await client.post("/api/auth/register/customer", json={"name": "Lee", "email": "lee@example.com", "password": PASSWORD})

    bad = await client.post("/api/auth/login", json={"email": "lee@example.com", "password": "wrong-pass"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "INVALID_CREDENTIALS"

    login = await client.post("/api/auth/login", json={"email": "LEE@example.com", "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["session"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["email"] == "lee@example.com"

    updated = await client.put("/api/auth/me", json={"name": "Lee Updated", "phone": "+15550001111"}, headers=headers)
    assert updated.status_code == 200 and updated.json()["name"] == "Lee Updated"

    refreshed = await client.post("/api/auth/refresh", json={"refresh_token": login.json()["session"]["refresh_token"]})
    assert refreshed.status_code == 200 and refreshed.json()["user"]["email"] == "lee@example.com"

    logout = await client.post("/api/auth/logout", headers=headers)
    assert logout.status_code == 204
    assert fake_auth.signed_out == [token]


async def test_admin_cannot_self_register(client):
    # There is no /register/admin route at all.
    response = await client.post("/api/auth/register/admin", json={})
    assert response.status_code in (404, 405)


async def test_deactivated_user_cannot_login_or_call_api(client, factory, fake_auth, db):
    user = await factory.user(UserRole.CUSTOMER, is_active=False)
    response = await client.get("/api/auth/me", headers=auth_headers(user))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"
