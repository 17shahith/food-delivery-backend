"""Shared FastAPI dependencies: authentication, role checks, pagination and service factories."""

from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenPayload, verify_access_token
from app.database.connection import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.services.admin_service import AdminService
from app.services.auth_service import AuthService
from app.services.notification_service import NotificationService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.realtime_service import RealtimePublisher, get_realtime_publisher
from app.services.rider_service import RiderService
from app.services.supabase_auth import SupabaseAuthClient, get_auth_client
from app.services.tracking_service import TrackingService
from app.services.vendor_service import VendorService
from app.utils.exceptions import AuthenticationError, PermissionDeniedError

bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token: `Bearer <jwt>`")

DbSession = Annotated[AsyncSession, Depends(get_db)]
Publisher = Annotated[RealtimePublisher, Depends(get_realtime_publisher)]


# ---------------------------------------------------------------------------- authentication
async def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> str:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing bearer token", code="TOKEN_MISSING")
    return credentials.credentials


async def get_token_payload(token: Annotated[str, Depends(get_bearer_token)]) -> TokenPayload:
    return await verify_access_token(token)


async def _load_user(payload: TokenPayload, db: AsyncSession) -> User:
    user = await db.get(User, payload.sub)
    if user is None:
        raise PermissionDeniedError(
            "No application profile exists for this account", code="PROFILE_NOT_FOUND"
        )
    if not user.is_active:
        raise PermissionDeniedError("This account has been deactivated", code="ACCOUNT_DISABLED")
    return user


async def get_current_user(payload: Annotated[TokenPayload, Depends(get_token_payload)], db: DbSession) -> User:
    """Role comes from the `users` table, never from user-editable JWT metadata."""
    return await _load_user(payload, db)


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)], db: DbSession
) -> User | None:
    if credentials is None:
        return None
    return await _load_user(await verify_access_token(credentials.credentials), db)


def require_roles(*roles: UserRole):
    async def checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise PermissionDeniedError(
                f"This action requires role: {', '.join(r.value for r in roles)}", code="ROLE_FORBIDDEN"
            )
        return user

    return checker


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
CustomerUser = Annotated[User, Depends(require_roles(UserRole.CUSTOMER))]
RiderUser = Annotated[User, Depends(require_roles(UserRole.RIDER))]
VendorOrAdmin = Annotated[User, Depends(require_roles(UserRole.VENDOR, UserRole.ADMIN))]
RiderOrAdmin = Annotated[User, Depends(require_roles(UserRole.RIDER, UserRole.ADMIN))]


# ---------------------------------------------------------------------------- pagination
class Pagination:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=100, description="Page size")] = 20,
        offset: Annotated[int, Query(ge=0, description="Items to skip")] = 0,
    ):
        self.limit = limit
        self.offset = offset


PaginationDep = Annotated[Pagination, Depends()]


# ---------------------------------------------------------------------------- services
def get_auth_service(
    db: DbSession, publisher: Publisher, client: Annotated[SupabaseAuthClient, Depends(get_auth_client)]
) -> AuthService:
    return AuthService(db, publisher, client)


def get_vendor_service(db: DbSession, publisher: Publisher) -> VendorService:
    return VendorService(db, publisher)


def get_product_service(db: DbSession, publisher: Publisher) -> ProductService:
    return ProductService(db, publisher)


def get_order_service(db: DbSession, publisher: Publisher) -> OrderService:
    return OrderService(db, publisher)


def get_rider_service(db: DbSession, publisher: Publisher) -> RiderService:
    return RiderService(db, publisher)


def get_tracking_service(db: DbSession, publisher: Publisher) -> TrackingService:
    return TrackingService(db, publisher)


def get_notification_service(db: DbSession, publisher: Publisher) -> NotificationService:
    return NotificationService(db, publisher)


def get_admin_service(db: DbSession, publisher: Publisher) -> AdminService:
    return AdminService(db, publisher)
