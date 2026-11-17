from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, get_auth_service, get_bearer_token
from app.models.enums import UserRole
from app.schemas.auth import AuthResponse, CustomerRegister, LoginRequest, RefreshRequest, RiderRegister, VendorRegister
from app.schemas.common import AUTH_ERRORS, error_responses
from app.schemas.user import UserOut, UserUpdate
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])
Service = Annotated[AuthService, Depends(get_auth_service)]

REGISTER_ERRORS = error_responses(400, 409, 422, 429, 503)


@router.post(
    "/register/customer",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    responses=REGISTER_ERRORS,
    summary="Register a customer",
    description="Creates the Supabase Auth user and the application profile. `session` is null when "
    "the Supabase project requires email confirmation.",
)
async def register_customer(payload: CustomerRegister, service: Service) -> AuthResponse:
    return await service.register(UserRole.CUSTOMER, payload)


@router.post(
    "/register/vendor",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    responses=REGISTER_ERRORS,
    summary="Register a vendor (optionally with shop details)",
    description="If `shop` is provided, the shop is created in `pending` status awaiting admin approval.",
)
async def register_vendor(payload: VendorRegister, service: Service) -> AuthResponse:
    return await service.register(UserRole.VENDOR, payload)


@router.post(
    "/register/rider",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    responses=REGISTER_ERRORS,
    summary="Register a delivery rider",
    description="Creates the rider profile in `pending` status; an admin must approve it before the rider can go online.",
)
async def register_rider(payload: RiderRegister, service: Service) -> AuthResponse:
    return await service.register(UserRole.RIDER, payload)


@router.post(
    "/login",
    response_model=AuthResponse,
    responses=error_responses(400, 401, 403, 422, 429, 503),
    summary="Log in with email and password",
)
async def login(payload: LoginRequest, service: Service) -> AuthResponse:
    return await service.login(payload.email, payload.password)


@router.post(
    "/refresh",
    response_model=AuthResponse,
    responses=error_responses(400, 401, 403, 422, 503),
    summary="Exchange a refresh token for a new session",
)
async def refresh(payload: RefreshRequest, service: Service) -> AuthResponse:
    return await service.refresh(payload.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(401, 403, 503),
    summary="Log out (revokes the Supabase refresh tokens of this session)",
)
async def logout(_: CurrentUser, token: Annotated[str, Depends(get_bearer_token)], service: Service) -> Response:
    await service.logout(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut, responses=error_responses(*AUTH_ERRORS), summary="Current user profile")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.put("/me", response_model=UserOut, responses=error_responses(*AUTH_ERRORS), summary="Update own profile")
async def update_me(payload: UserUpdate, user: CurrentUser, service: Service) -> UserOut:
    return UserOut.model_validate(await service.update_profile(user, payload))
