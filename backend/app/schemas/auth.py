from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import VehicleType
from app.schemas.common import Phone
from app.schemas.user import UserOut
from app.schemas.vendor import VendorShopDetails


class RegisterBase(BaseModel):
    name: str = Field(min_length=2, max_length=120, examples=["Asha Kumar"])
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    phone: Phone | None = None

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class CustomerRegister(RegisterBase):
    pass


class VendorRegister(RegisterBase):
    shop: VendorShopDetails | None = Field(
        None, description="Optional shop details; the shop can also be created later via POST /api/vendors"
    )


class RiderRegister(RegisterBase):
    vehicle_type: VehicleType
    vehicle_number: str | None = Field(None, max_length=30)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class AuthSession(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    expires_in: int | None = None
    expires_at: int | None = None


class AuthResponse(BaseModel):
    user: UserOut | None
    session: AuthSession | None
    email_confirmation_required: bool = False
