"""Product image uploads to Supabase Storage (public bucket, written with the service-role key)."""

import logging
import uuid

import httpx

from app.core.config import Settings, get_settings
from app.utils.exceptions import BadRequestError, ExternalServiceError

logger = logging.getLogger(__name__)

# Detect the real type from the file's magic bytes; the client-supplied content type is not trusted.
_SIGNATURES: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)


def detect_image(data: bytes) -> tuple[str, str] | None:
    for signature, content_type, ext in _SIGNATURES:
        if data.startswith(signature):
            return content_type, ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


class StorageService:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def upload_product_image(self, vendor_id: uuid.UUID, data: bytes) -> str:
        s = self.settings
        if not data:
            raise BadRequestError("No file uploaded", code="EMPTY_FILE")
        if len(data) > s.MAX_UPLOAD_MB * 1024 * 1024:
            raise BadRequestError(f"Images must be {s.MAX_UPLOAD_MB:g} MB or smaller", code="FILE_TOO_LARGE")
        detected = detect_image(data)
        if detected is None:
            raise BadRequestError("Upload a JPEG, PNG, WebP or GIF image", code="UNSUPPORTED_FILE_TYPE")
        if not s.SUPABASE_URL or not s.SUPABASE_SERVICE_ROLE_KEY:
            raise ExternalServiceError("Image storage is not configured")

        content_type, ext = detected
        path = f"{vendor_id}/{uuid.uuid4().hex}.{ext}"
        base = s.SUPABASE_URL.rstrip("/")
        key = s.SUPABASE_SERVICE_ROLE_KEY
        try:
            response = await self._get_client().post(
                f"{base}/storage/v1/object/{s.STORAGE_BUCKET}/{path}",
                content=data,
                headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": content_type,
                         "x-upsert": "false", "cache-control": "max-age=31536000"},
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceError("Image storage is unavailable") from exc
        if response.status_code >= 400:
            logger.warning("Storage upload failed (%s): %s", response.status_code, response.text[:300])
            raise ExternalServiceError("Could not store the image")
        return f"{base}/storage/v1/object/public/{s.STORAGE_BUCKET}/{path}"

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_storage: StorageService | None = None


def get_storage_service() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage


async def close_storage_service() -> None:
    if _storage is not None:
        await _storage.aclose()
