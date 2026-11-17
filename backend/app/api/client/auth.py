import uuid
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Response, status

from app.api.client.deps import Client
from app.api.deps import CurrentUser, CustomerUser, get_auth_service, get_bearer_token
from app.core.config import get_settings
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import CustomerRegister, RiderRegister, VendorRegister
from app.schemas.client import (
    AddressIn,
    AddressOut,
    AddressPatch,
    AppUser,
    AuthTokens,
    ChangePasswordIn,
    ForgotPasswordIn,
    LoginIn,
    MessageOut,
    ProfilePatch,
    RefreshIn,
    RegisterIn,
    TokenPair,
)
from app.schemas.common import error_responses
from app.schemas.user import UserUpdate
from app.schemas.vendor import VendorShopDetails
from app.services.auth_service import AuthService
from app.services.client_mappers import domain_vehicle, to_address, to_app_user
from app.utils.exceptions import (
    AuthenticationError,
    BadRequestError,
    ExternalServiceError,
    PermissionDeniedError,
    RateLimitedError,
)

router = APIRouter(tags=["App: Auth & profile"])
Auth = Annotated[AuthService, Depends(get_auth_service)]

@router.post(
    "/auth/register",
    response_model=AuthTokens | MessageOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(400, 409, 422, 429, 503),
    summary="Register (customer / vendor with store / rider with vehicle)",
    description="Returns tokens, or `{message}` when the Supabase project requires email confirmation.",
)
async def register(payload: RegisterIn, auth: Auth, client: Client) -> AuthTokens | MessageOut:
    common = {"name": payload.full_name, "email": payload.email, "password": payload.password, "phone": payload.phone}
    if payload.role == "vendor":
        shop = VendorShopDetails(
            shop_name=payload.store_name, address=payload.store_address, latitude=payload.latitude,
            longitude=payload.longitude, category=payload.store_category or "general",
        )
        result = await auth.register(UserRole.VENDOR, VendorRegister(**common, shop=shop))
    elif payload.role == "rider":
        result = await auth.register(
            UserRole.RIDER,
            RiderRegister(**common, vehicle_type=domain_vehicle(payload.vehicle_type), vehicle_number=payload.vehicle_number),
        )
    else:
        result = await auth.register(UserRole.CUSTOMER, CustomerRegister(**common))

    if result.session is None:
        return MessageOut(message="Check your email to confirm your account, then sign in.")
    user = await client.session.get(User, result.user.id)
    return AuthTokens(
        access_token=result.session.access_token,
        refresh_token=result.session.refresh_token,
        token_type=result.session.token_type,
        user=to_app_user(user, await client.approval_for(user)),
    )

@router.post("/auth/login", response_model=AuthTokens, responses=error_responses(401, 403, 422, 429, 503))
async def login(payload: LoginIn, auth: Auth, client: Client) -> AuthTokens:
    result = await auth.login(payload.email, payload.password)
    if result.user is None:
        raise PermissionDeniedError("No profile exists for this account", code="PROFILE_NOT_FOUND")
    user = await client.session.get(User, result.user.id)
    return AuthTokens(
        access_token=result.session.access_token,
        refresh_token=result.session.refresh_token,
        token_type=result.session.token_type,
        user=to_app_user(user, await client.approval_for(user)),
    )

@router.post("/auth/refresh", response_model=TokenPair, responses=error_responses(401, 403, 503))
async def refresh(payload: RefreshIn, auth: Auth) -> TokenPair:
    result = await auth.refresh(payload.refresh_token)
    return TokenPair(access_token=result.session.access_token, refresh_token=result.session.refresh_token)

@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, responses=error_responses(401))
async def logout(_: CurrentUser, token: Annotated[str, Depends(get_bearer_token)], auth: Auth) -> Response:
    try:
        await auth.logout(token)
    except (AuthenticationError, ExternalServiceError):
        pass  # the client discards its tokens regardless
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.get("/auth/me", response_model=AppUser, responses=error_responses(401, 403))
async def me(user: CurrentUser, client: Client) -> AppUser:
    return to_app_user(user, await client.approval_for(user))

@router.patch("/auth/me", response_model=AppUser, responses=error_responses(401, 403, 422))
async def update_me(payload: ProfilePatch, user: CurrentUser, auth: Auth, client: Client) -> AppUser:
    changes = payload.model_dump(exclude_unset=True)
    if "avatar_url" in changes:
        user.avatar_url = changes.pop("avatar_url")
    update = UserUpdate(name=changes.get("full_name"), phone=changes.get("phone"))
    await auth.update_profile(user, UserUpdate(**update.model_dump(exclude_none=True)))
    return to_app_user(user, await client.approval_for(user))

@router.post(
    "/auth/change-password",
    response_model=MessageOut,
    responses=error_responses(400, 401, 422, 429, 503),
)
async def change_password(payload: ChangePasswordIn, user: CurrentUser, auth: Auth) -> MessageOut:
    try:
        session = await auth.auth_client.sign_in(user.email, payload.current_password)
    except AuthenticationError as exc:
        raise BadRequestError("Current password is incorrect", code="WRONG_PASSWORD") from exc
    await auth.auth_client.update_password(session["access_token"], payload.new_password)
    return MessageOut(message="Password updated")

@router.post("/auth/forgot-password", response_model=MessageOut, responses=error_responses(422, 429, 503))
async def forgot_password(payload: ForgotPasswordIn, auth: Auth) -> MessageOut:
    redirect = payload.redirect_to
    if redirect:
        parts = urlsplit(redirect)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in get_settings().ALLOWED_ORIGINS:
            redirect = None  # never send recovery links to unknown sites
    try:
        await auth.auth_client.send_recovery_email(payload.email, redirect)
    except (RateLimitedError, ExternalServiceError):
        raise
    except Exception:  # noqa: BLE001 - do not reveal whether the account exists
        pass
    return MessageOut(message="If an account exists for that email, a reset link has been sent.")

# ----------------------------------------------------------------------------- address book
@router.get("/customers/me/addresses", response_model=list[AddressOut], tags=["App: Customers"])
async def list_addresses(user: CustomerUser, client: Client) -> list[AddressOut]:
    return [to_address(a) for a in await client.list_addresses(user)]

@router.post(
    "/customers/me/addresses", response_model=AddressOut, status_code=status.HTTP_201_CREATED, tags=["App: Customers"]
)
async def create_address(payload: AddressIn, user: CustomerUser, client: Client) -> AddressOut:
    return to_address(await client.create_address(user, payload))

@router.patch("/customers/me/addresses/{address_id}", response_model=AddressOut, tags=["App: Customers"])
async def update_address(address_id: uuid.UUID, payload: AddressPatch, user: CustomerUser, client: Client) -> AddressOut:

    return to_address(await client.update_address(user, address_id, payload))

@router.delete("/customers/me/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["App: Customers"])
async def delete_address(address_id: uuid.UUID, user: CustomerUser, client: Client) -> Response:

    await client.delete_address(user, address_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
