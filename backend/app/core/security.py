"""Verification of Supabase-issued access tokens (JWT).

Supports both the legacy shared-secret signing (HS256, `SUPABASE_JWT_SECRET`) and
asymmetric signing keys (RS256/ES256) published at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`.
"""

import uuid

import jwt
from jwt import PyJWKClient
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.utils.exceptions import AuthenticationError

ASYMMETRIC_ALGORITHMS = {"RS256", "ES256"}
_jwks_client: PyJWKClient | None = None


class TokenPayload(BaseModel):
    sub: uuid.UUID
    email: str | None = None
    role: str | None = None  # Supabase Postgres role ("authenticated"), NOT the application role
    session_id: str | None = None
    exp: int


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    settings = get_settings()
    if not settings.SUPABASE_URL:
        raise AuthenticationError("Asymmetric token received but SUPABASE_URL is not configured")
    if _jwks_client is None:
        _jwks_client = PyJWKClient(f"{settings.supabase_auth_url}/.well-known/jwks.json", cache_keys=True)
    return _jwks_client


def decode_access_token(token: str) -> TokenPayload:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm == "HS256":
            if not settings.SUPABASE_JWT_SECRET:
                raise AuthenticationError("HS256 tokens are not accepted: SUPABASE_JWT_SECRET is not set")
            key: object = settings.SUPABASE_JWT_SECRET
        elif algorithm in ASYMMETRIC_ALGORITHMS:
            key = _get_jwks_client().get_signing_key_from_jwt(token).key
        else:
            raise AuthenticationError("Unsupported token algorithm")

        options = {"require": ["exp", "sub"]}
        kwargs: dict = {"algorithms": [algorithm], "audience": settings.SUPABASE_JWT_AUDIENCE}
        if settings.jwt_issuer:
            kwargs["issuer"] = settings.jwt_issuer
        claims = jwt.decode(token, key, options=options, **kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Access token has expired", code="TOKEN_EXPIRED") from exc
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationError("Invalid access token", code="INVALID_TOKEN") from exc

    try:
        return TokenPayload.model_validate(claims)
    except ValueError as exc:
        raise AuthenticationError("Invalid access token claims", code="INVALID_TOKEN") from exc


async def verify_access_token(token: str) -> TokenPayload:
    """Async wrapper; JWKS fetches are blocking network calls, so run them off the event loop."""
    try:
        algorithm = jwt.get_unverified_header(token).get("alg")
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid access token", code="INVALID_TOKEN") from exc
    if algorithm in ASYMMETRIC_ALGORITHMS:
        return await run_in_threadpool(decode_access_token, token)
    return decode_access_token(token)
