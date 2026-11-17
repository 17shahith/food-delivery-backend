"""JWT verification, role-based authorization and unauthorized access."""

import time
import uuid

import jwt
import pytest

from app.models.enums import UserRole
from tests.conftest import auth_headers, make_token


async def test_missing_token_returns_401(client):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_MISSING"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_malformed_token_returns_401(client):
    response = await client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_TOKEN"


async def test_expired_token_returns_401(client, customer):
    token = make_token(customer.id, expires_in=-60)
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.parametrize(
    "overrides",
    [{"aud": "anon"}, {"iss": "https://evil.example.com/auth/v1"}],
    ids=["wrong-audience", "wrong-issuer"],
)
async def test_wrong_claims_rejected(client, customer, overrides):
    token = make_token(customer.id, **overrides)
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_wrong_signature_rejected(client, customer):
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(customer.id), "aud": "authenticated", "exp": now + 3600}, "another-secret-of-sufficient-length!", "HS256"
    )
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_unsigned_token_rejected(client, customer):
    token = jwt.encode({"sub": str(customer.id), "aud": "authenticated", "exp": int(time.time()) + 60}, None, "none")
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_valid_token_without_profile_is_forbidden(client):
    response = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {make_token(uuid.uuid4())}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROFILE_NOT_FOUND"


async def test_role_comes_from_database_not_token(client, customer):
    # Even if the JWT carries an admin-looking claim, the DB role (customer) is enforced.
    token = make_token(customer.id, user_metadata={"role": "admin"}, app_metadata={"role": "admin"})
    response = await client.get("/api/admin/analytics", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("role", "method", "path", "body"),
    [
        (UserRole.CUSTOMER, "post", "/api/products", {"name": "x", "price": 1}),
        (UserRole.CUSTOMER, "post", "/api/vendors", {"shop_name": "xx", "address": "12345", "latitude": 1, "longitude": 1}),
        (UserRole.CUSTOMER, "post", "/api/tracking/location", {"order_id": str(uuid.uuid4()), "latitude": 1, "longitude": 1}),
        (UserRole.CUSTOMER, "get", "/api/riders", None),
        (UserRole.VENDOR, "post", "/api/orders", {"items": [{"product_id": str(uuid.uuid4()), "quantity": 1}], "delivery_address": "somewhere"}),
        (UserRole.RIDER, "post", "/api/riders/assign", {"order_id": str(uuid.uuid4()), "rider_id": str(uuid.uuid4())}),
        (UserRole.VENDOR, "get", "/api/admin/users", None),
        (UserRole.RIDER, "get", "/api/admin/analytics", None),
        (UserRole.CUSTOMER, "post", "/api/notifications", {"role": "customer", "title": "t", "message": "m"}),
        (UserRole.CUSTOMER, "patch", f"/api/vendors/{uuid.uuid4()}/status", {"status": "approved"}),
    ],
)
async def test_role_based_access_control(client, factory, role, method, path, body):
    user = await factory.user(role)
    kwargs = {"headers": auth_headers(user)}
    if body is not None:
        kwargs["json"] = body
    response = await getattr(client, method)(path, **kwargs)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ROLE_FORBIDDEN"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/orders"),
        ("post", "/api/orders"),
        ("get", "/api/notifications"),
        ("post", "/api/tracking/location"),
        ("get", f"/api/tracking/{uuid.uuid4()}"),
        ("get", "/api/admin/analytics"),
        ("post", "/api/riders/assign"),
    ],
)
async def test_protected_endpoints_require_authentication(client, method, path):
    response = await getattr(client, method)(path)
    assert response.status_code == 401


async def test_security_headers_and_request_id(client):
    response = await client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert response.headers["x-request-id"] == "abc123"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_openapi_docs_available(client):
    assert (await client.get("/docs")).status_code == 200
    spec = (await client.get("/openapi.json")).json()
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    assert "ErrorResponse" in spec["components"]["schemas"]
