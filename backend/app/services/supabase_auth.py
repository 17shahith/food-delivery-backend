"""Thin async client for the Supabase Auth (GoTrue) REST API."""

import logging
import uuid
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.utils.exceptions import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    ExternalServiceError,
    RateLimitedError,
)

logger = logging.getLogger(__name__)


class SupabaseAuthClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        bearer: str | None = None,
        service: bool = False,
    ) -> dict[str, Any]:
        s = self.settings
        api_key = s.SUPABASE_SERVICE_ROLE_KEY if service else s.SUPABASE_ANON_KEY
        if not s.SUPABASE_URL or not api_key:
            raise ExternalServiceError("Supabase authentication is not configured")
        headers = {"apikey": api_key, "Authorization": f"Bearer {bearer or api_key}"}
        try:
            response = await self._get_client().request(
                method, f"{s.supabase_auth_url}{path}", json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            logger.warning("Supabase auth request failed: %s", exc)
            raise ExternalServiceError("Authentication service is unavailable") from exc
        if response.status_code >= 400:
            self._raise_for_response(response)
        return response.json() if response.content else {}

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = (
            body.get("msg")
            or body.get("error_description")
            or body.get("message")
            or body.get("error")
            or "Authentication request failed"
        )
        error_code = str(body.get("error_code") or "")
        status = response.status_code
        lowered = message.lower()
        if status == 429:
            raise RateLimitedError(message)
        if error_code == "invalid_credentials" or "invalid login" in lowered:
            raise AuthenticationError("Invalid email or password", code="INVALID_CREDENTIALS")
        if error_code in {"user_already_exists", "email_exists"} or "already registered" in lowered:
            raise ConflictError("An account with this email already exists", code="EMAIL_EXISTS")
        if error_code == "email_not_confirmed":
            raise AuthenticationError("Email address has not been confirmed", code="EMAIL_NOT_CONFIRMED")
        if status in (400, 422):
            raise BadRequestError(message, code=error_code.upper() or "AUTH_REQUEST_INVALID")
        if status in (401, 403):
            raise AuthenticationError(message)
        raise ExternalServiceError("Authentication service error")

    async def sign_up(self, email: str, password: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/signup", json={"email": email, "password": password, "data": metadata})

    async def sign_in(self, email: str, password: str) -> dict[str, Any]:
        return await self._request("POST", "/token?grant_type=password", json={"email": email, "password": password})

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._request("POST", "/token?grant_type=refresh_token", json={"refresh_token": refresh_token})

    async def sign_out(self, access_token: str) -> None:
        await self._request("POST", "/logout", bearer=access_token)

    async def update_password(self, access_token: str, new_password: str) -> None:
        """Change the password of the user owning `access_token` (GoTrue `PUT /user`)."""
        await self._request("PUT", "/user", json={"password": new_password}, bearer=access_token)

    async def send_recovery_email(self, email: str, redirect_to: str | None) -> None:
        """Send a password-recovery link; Supabase only honours redirect URLs allow-listed in the project."""
        path = "/recover"
        if redirect_to:
            path += f"?{httpx.QueryParams({'redirect_to': redirect_to})}"
        await self._request("POST", path, json={"email": email})

    async def admin_delete_user(self, user_id: uuid.UUID) -> None:
        """Compensating action when the local profile cannot be created after sign-up."""
        await self._request("DELETE", f"/admin/users/{user_id}", service=True)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_auth_client: SupabaseAuthClient | None = None


def get_auth_client() -> SupabaseAuthClient:
    global _auth_client
    if _auth_client is None:
        _auth_client = SupabaseAuthClient()
    return _auth_client


async def close_auth_client() -> None:
    if _auth_client is not None:
        await _auth_client.aclose()
