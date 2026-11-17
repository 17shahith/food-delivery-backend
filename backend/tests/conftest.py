"""Test harness.

* SQLite (in-memory, FK enforcement on) replaces Supabase Postgres.
* Supabase Auth is replaced by `FakeSupabaseAuth`, which issues real HS256 JWTs signed with the
  test secret, so the production JWT verification path is exercised end-to-end.
* Supabase Realtime is replaced by `RecordingPublisher`, which captures published events.
"""

import os

os.environ.update(
    {
        "DATABASE_URL": "sqlite+aiosqlite://",
        "SUPABASE_URL": "https://test-project.supabase.co",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
        "SUPABASE_JWT_SECRET": "test-jwt-secret-that-is-long-enough-for-hs256",
        "REALTIME_ENABLED": "true",
        "ENVIRONMENT": "test",
    }
)

import time  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from datetime import timedelta  # noqa: E402
from decimal import Decimal  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.connection import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Product, Rider, User, Vendor  # noqa: E402
from app.models.enums import ApprovalStatus, RiderAvailability, UserRole, VehicleType  # noqa: E402
from app.services.realtime_service import RealtimeEvent, get_realtime_publisher  # noqa: E402
from app.services.storage_service import get_storage_service  # noqa: E402
from app.services.supabase_auth import get_auth_client  # noqa: E402
from app.utils.exceptions import AuthenticationError, ConflictError  # noqa: E402
from app.utils.helpers import utcnow  # noqa: E402

settings = get_settings()


def make_token(user_id: uuid.UUID, *, email: str = "user@example.com", expires_in: int = 3600, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "email": email,
        "aud": "authenticated",
        "role": "authenticated",
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + expires_in,
        "session_id": str(uuid.uuid4()),
    }
    claims.update(overrides)
    return jwt.encode(claims, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


def auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user.id, email=user.email)}"}


class FakeSupabaseAuth:
    def __init__(self):
        self.accounts: dict[str, dict] = {}
        self.signed_out: list[str] = []
        self.deleted: list[uuid.UUID] = []
        self.recovery_requests: list[tuple[str, str | None]] = []
        self.require_confirmation = False

    def _session(self, user_id: uuid.UUID, email: str) -> dict:
        return {
            "access_token": make_token(user_id, email=email),
            "refresh_token": f"refresh-{user_id}",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {"id": str(user_id), "email": email},
        }

    async def sign_up(self, email, password, metadata):
        if email in self.accounts:
            raise ConflictError("An account with this email already exists", code="EMAIL_EXISTS")
        user_id = uuid.uuid4()
        self.accounts[email] = {"id": user_id, "password": password, "metadata": metadata}
        if self.require_confirmation:
            return {"id": str(user_id), "email": email}
        return self._session(user_id, email)

    async def sign_in(self, email, password):
        account = self.accounts.get(email)
        if account is None or account["password"] != password:
            raise AuthenticationError("Invalid email or password", code="INVALID_CREDENTIALS")
        return self._session(account["id"], email)

    async def refresh(self, refresh_token):
        for email, account in self.accounts.items():
            if refresh_token == f"refresh-{account['id']}":
                return self._session(account["id"], email)
        raise AuthenticationError("Invalid refresh token")

    async def sign_out(self, access_token):
        self.signed_out.append(access_token)

    async def update_password(self, access_token, new_password):
        user_id = uuid.UUID(jwt.decode(access_token, options={"verify_signature": False})["sub"])
        for account in self.accounts.values():
            if account["id"] == user_id:
                account["password"] = new_password
                return
        raise AuthenticationError("Unknown user")

    async def send_recovery_email(self, email, redirect_to):
        self.recovery_requests.append((email, redirect_to))

    async def admin_delete_user(self, user_id):
        self.deleted.append(user_id)


class RecordingPublisher:
    def __init__(self):
        self.events: list[dict] = []

    async def publish(self, events: list[RealtimeEvent]) -> bool:
        self.events.extend(e.resolve() for e in events)
        return True

    def of(self, event: str, topic: str | None = None) -> list[dict]:
        return [e for e in self.events if e["event"] == event and (topic is None or e["topic"] == topic)]


class FakeStorage:
    """Stands in for Supabase Storage; reuses the real validation (size + magic bytes)."""

    def __init__(self):
        from app.services.storage_service import StorageService

        self.uploads: list[tuple[uuid.UUID, int]] = []
        self._real = StorageService(settings)
        self.settings = settings

    async def upload_product_image(self, vendor_id, data):
        from app.services.storage_service import detect_image
        from app.utils.exceptions import BadRequestError

        if not data or len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024 or detect_image(data) is None:
            raise BadRequestError("Upload a JPEG, PNG, WebP or GIF image", code="UNSUPPORTED_FILE_TYPE")
        self.uploads.append((vendor_id, len(data)))
        return f"https://test-project.supabase.co/storage/v1/object/public/product-images/{vendor_id}/img.png"


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def fake_auth() -> FakeSupabaseAuth:
    return FakeSupabaseAuth()


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
async def client(session_factory, fake_auth, publisher, storage) -> AsyncIterator[AsyncClient]:
    async def _get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_auth_client] = lambda: fake_auth
    app.dependency_overrides[get_realtime_publisher] = lambda: publisher
    app.dependency_overrides[get_storage_service] = lambda: storage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------- data factories
class Factory:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._n = 0

    async def user(self, role: UserRole, **kw) -> User:
        self._n += 1
        user = User(
            id=uuid.uuid4(),
            name=kw.pop("name", f"{role.value.title()} {self._n}"),
            email=kw.pop("email", f"{role.value}{self._n}-{uuid.uuid4().hex[:6]}@example.com"),
            phone=kw.pop("phone", "+919800000000"),
            role=role,
            **kw,
        )
        self.db.add(user)
        await self.db.commit()
        return user

    async def vendor(self, owner: User | None = None, *, status=ApprovalStatus.APPROVED, is_open=True,
                     latitude=12.9716, longitude=77.5946, shop_name=None) -> Vendor:
        owner = owner or await self.user(UserRole.VENDOR)
        vendor = Vendor(
            user_id=owner.id, shop_name=shop_name or f"Shop {self._n}", description="Fresh groceries",
            address="12 MG Road, Bengaluru", latitude=latitude, longitude=longitude, status=status, is_open=is_open,
        )
        self.db.add(vendor)
        await self.db.commit()
        return vendor

    async def product(self, vendor: Vendor, *, price="50.00", availability=True, name=None, category="grocery") -> Product:
        self._n += 1
        product = Product(
            vendor_id=vendor.id, name=name or f"Product {self._n}", description="Tasty", category=category,
            price=Decimal(price), availability=availability,
        )
        self.db.add(product)
        await self.db.commit()
        return product

    async def rider(self, owner: User | None = None, *, status=ApprovalStatus.APPROVED,
                    availability=RiderAvailability.ONLINE, latitude=12.972, longitude=77.595) -> Rider:
        owner = owner or await self.user(UserRole.RIDER)
        rider = Rider(
            user_id=owner.id, vehicle_type=VehicleType.MOTORCYCLE, vehicle_number="KA01AB1234", status=status,
            availability=availability, latitude=latitude, longitude=longitude,
            last_location_at=utcnow() - timedelta(minutes=1),
        )
        self.db.add(rider)
        await self.db.commit()
        return rider


@pytest.fixture
def factory(db) -> Factory:
    return Factory(db)


@pytest.fixture
async def customer(factory) -> User:
    return await factory.user(UserRole.CUSTOMER)


@pytest.fixture
async def admin(factory) -> User:
    return await factory.user(UserRole.ADMIN)


@pytest.fixture
async def vendor_user(factory) -> User:
    return await factory.user(UserRole.VENDOR)


@pytest.fixture
async def shop(factory, vendor_user) -> Vendor:
    return await factory.vendor(vendor_user)


@pytest.fixture
async def rider_user(factory) -> User:
    return await factory.user(UserRole.RIDER)


@pytest.fixture
async def rider(factory, rider_user) -> Rider:
    return await factory.rider(rider_user)


# ---------------------------------------------------------------------------- flow helpers
async def place_order(client, customer, products_qty, **extra) -> dict:
    body = {
        "items": [{"product_id": str(p.id), "quantity": q} for p, q in products_qty],
        "delivery_address": "221B Baker Street, Bengaluru",
        "delivery_latitude": 12.9352,
        "delivery_longitude": 77.6245,
        **extra,
    }
    response = await client.post("/api/orders", json=body, headers=auth_headers(customer))
    assert response.status_code == 201, response.text
    return response.json()


async def set_status(client, user, order_id, status):
    return await client.put(f"/api/orders/{order_id}/status", json={"status": status}, headers=auth_headers(user))


async def advance_to_ready(client, vendor_user, order_id):
    for status in ("VENDOR_ACCEPTED", "PREPARING", "READY_FOR_PICKUP"):
        response = await set_status(client, vendor_user, order_id, status)
        assert response.status_code == 200, response.text


async def assign_and_accept(client, vendor_user, rider_user, order_id, rider_id) -> dict:
    response = await client.post(
        "/api/riders/assign", json={"order_id": order_id, "rider_id": str(rider_id)}, headers=auth_headers(vendor_user)
    )
    assert response.status_code == 201, response.text
    assignment = response.json()
    response = await client.post(f"/api/riders/assignments/{assignment['id']}/accept", headers=auth_headers(rider_user))
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def delivery(client, factory, customer, vendor_user, shop, rider_user, rider) -> dict:
    """An order that is RIDER_ASSIGNED to `rider`."""
    product = await factory.product(shop)
    checkout = await place_order(client, customer, [(product, 2)])
    order_id = checkout["orders"][0]["id"]
    await advance_to_ready(client, vendor_user, order_id)
    await assign_and_accept(client, vendor_user, rider_user, order_id, rider.id)
    return {"order_id": order_id, "product": product}
