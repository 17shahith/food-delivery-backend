"""Small, dependency-free helpers: geo maths, identifiers, time and text."""

import math
import secrets
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Cheap rectangular pre-filter (min_lat, max_lat, min_lon, max_lon) for radius searches."""
    d_lat = radius_km / 111.32
    d_lon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    return lat - d_lat, lat + d_lat, lon - d_lon, lon + d_lon


def generate_order_number(now: datetime | None = None) -> str:
    now = now or utcnow()
    return f"ORD-{now:%Y%m%d}-{secrets.token_hex(4).upper()}"


def generate_checkout_reference(now: datetime | None = None) -> str:
    now = now or utcnow()
    return f"CHK-{now:%Y%m%d}-{secrets.token_hex(5).upper()}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime) -> datetime:
    """Treat naive datetimes (e.g. from SQLite) as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
