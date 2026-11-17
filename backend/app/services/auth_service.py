import logging
import uuid
from typing import Any

from app.models.enums import ApprovalStatus, RiderAvailability, UserRole
from app.models.rider import Rider
from app.models.user import User
from app.models.vendor import Vendor
from app.repositories.user_repository import UserRepository
from app.schemas.auth import AuthResponse, AuthSession, RegisterBase, RiderRegister, VendorRegister
from app.schemas.user import UserOut, UserUpdate
from app.services.base import BaseService
from app.services.notification_service import NotificationService
from app.services.supabase_auth import SupabaseAuthClient
from app.utils.exceptions import ConflictError, ExternalServiceError, PermissionDeniedError

logger = logging.getLogger(__name__)


def _parse_session(data: dict[str, Any]) -> AuthSession | None:
    if not data.get("access_token"):
        return None
    return AuthSession(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        token_type=data.get("token_type", "bearer"),
        expires_in=data.get("expires_in"),
        expires_at=data.get("expires_at"),
    )


def _auth_user_id(data: dict[str, Any]) -> uuid.UUID:
    # GoTrue returns {"user": {...}, access_token...} with a session, or the bare user object
    # when email confirmation is required.
    auth_user = data.get("user") or data
    try:
        return uuid.UUID(str(auth_user["id"]))
    except (KeyError, ValueError) as exc:
        raise ExternalServiceError("Unexpected response from authentication service") from exc


class AuthService(BaseService):
    def __init__(self, session, publisher, auth_client: SupabaseAuthClient):
        super().__init__(session, publisher)
        self.auth_client = auth_client
        self.users = UserRepository(session)
        self.notifications = NotificationService(session, publisher)

    async def register(self, role: UserRole, payload: RegisterBase) -> AuthResponse:
        if role == UserRole.ADMIN:
            raise PermissionDeniedError("Administrators cannot self-register")
        if await self.users.get_by_email(payload.email):
            raise ConflictError("An account with this email already exists", code="EMAIL_EXISTS")

        data = await self.auth_client.sign_up(
            payload.email, payload.password, {"name": payload.name, "phone": payload.phone, "role": role.value}
        )
        user_id = _auth_user_id(data)

        user = User(id=user_id, name=payload.name, email=payload.email, phone=payload.phone, role=role, is_active=True)
        self.session.add(user)
        if isinstance(payload, VendorRegister) and payload.shop is not None:
            self.session.add(Vendor(user_id=user_id, status=ApprovalStatus.PENDING, is_open=False, **payload.shop.model_dump()))
            await self.notifications.notify_role(
                UserRole.ADMIN, "Vendor awaiting approval", f"{payload.shop.shop_name} registered and needs review.",
                type_="vendor_pending",
            )
        if isinstance(payload, RiderRegister):
            self.session.add(
                Rider(
                    user_id=user_id,
                    vehicle_type=payload.vehicle_type,
                    vehicle_number=payload.vehicle_number,
                    status=ApprovalStatus.PENDING,
                    availability=RiderAvailability.OFFLINE,
                )
            )
            await self.notifications.notify_role(
                UserRole.ADMIN, "Rider awaiting approval", f"{payload.name} registered as a rider and needs review.",
                type_="rider_pending",
            )

        try:
            await self.commit()
        except Exception:
            await self.rollback()
            try:
                await self.auth_client.admin_delete_user(user_id)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to roll back Supabase user %s after profile error", user_id)
            raise

        session = _parse_session(data)
        return AuthResponse(
            user=UserOut.model_validate(user), session=session, email_confirmation_required=session is None
        )

    async def login(self, email: str, password: str) -> AuthResponse:
        data = await self.auth_client.sign_in(email, password)
        return await self._session_response(data)

    async def refresh(self, refresh_token: str) -> AuthResponse:
        data = await self.auth_client.refresh(refresh_token)
        return await self._session_response(data)

    async def _session_response(self, data: dict[str, Any]) -> AuthResponse:
        user = await self.users.get(_auth_user_id(data))
        if user is not None and not user.is_active:
            raise PermissionDeniedError("This account has been deactivated", code="ACCOUNT_DISABLED")
        return AuthResponse(user=UserOut.model_validate(user) if user else None, session=_parse_session(data))

    async def logout(self, access_token: str) -> None:
        await self.auth_client.sign_out(access_token)

    async def update_profile(self, user: User, data: UserUpdate) -> User:
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        await self.commit()
        return user
