from decimal import Decimal
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

T = TypeVar("T")

# Monetary values are stored as exact decimals and emitted as JSON numbers for the frontend.
Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False, examples=[12.9716])]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False, examples=[77.5946])]
Phone = Annotated[str, Field(pattern=r"^\+?[0-9]{7,15}$", examples=["+919876543210"])]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    code: str = Field(examples=["NOT_FOUND"])
    message: str = Field(examples=["Order not found"])
    details: Any | None = None


class ErrorResponse(BaseModel):
    detail: Any = Field(description="Human-readable message (or validation error list), FastAPI style")
    error: ErrorDetail


class MessageResponse(BaseModel):
    message: str


_ERROR_DESCRIPTIONS = {
    400: "Bad request",
    401: "Missing, invalid or expired access token",
    403: "Authenticated but not allowed to perform this action",
    404: "Resource not found",
    409: "Conflict with the current state (e.g. invalid status transition, duplicate)",
    422: "Request validation failed",
    429: "Rate limited by an upstream service",
    503: "Upstream service (Supabase) unavailable",
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI `responses=` helper documenting the shared error envelope."""
    return {code: {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]} for code in codes}


AUTH_ERRORS = (401, 403, 422)
