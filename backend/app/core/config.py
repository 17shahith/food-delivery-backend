"""Application settings loaded from environment variables / .env."""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Hyperlocal Delivery Tracker API"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    API_PREFIX: str = "/api"

    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""
    SUPABASE_JWT_SECRET: str = ""
    SUPABASE_JWT_AUDIENCE: str = "authenticated"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10

    SECRET_KEY: str = ""
    ALLOWED_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # Realtime
    REALTIME_ENABLED: bool = True
    REALTIME_TIMEOUT_SECONDS: float = 3.0

    # GPS write throttling
    GPS_MIN_DISTANCE_METERS: float = 15.0
    GPS_MIN_INTERVAL_SECONDS: float = 3.0
    GPS_HEARTBEAT_SECONDS: float = 30.0

    # Rider assignment
    ASSIGNMENT_OFFER_TTL_SECONDS: int = 300
    RIDER_LOCATION_STALE_MINUTES: int = 15

    # Rider pay per delivery: base + per km (shop -> customer), shown to riders and in stats
    RIDER_BASE_PAY: float = 30.0
    RIDER_PAY_PER_KM: float = 8.0

    # Product image uploads (Supabase Storage, public bucket created by migration 0003)
    STORAGE_BUCKET: str = "product-images"
    MAX_UPLOAD_MB: float = 5.0

    # Geo search
    DEFAULT_SEARCH_RADIUS_KM: float = 5.0
    MAX_SEARCH_RADIUS_KM: float = 50.0

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def async_database_url(self) -> str:
        url = self.DATABASE_URL
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+asyncpg://" + url[len(prefix):]
        return url

    @property
    def supabase_auth_url(self) -> str:
        return f"{self.SUPABASE_URL.rstrip('/')}/auth/v1"

    @property
    def jwt_issuer(self) -> str | None:
        return self.supabase_auth_url if self.SUPABASE_URL else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
