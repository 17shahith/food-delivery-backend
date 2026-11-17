"""Supabase Realtime integration.

Two complementary channels reach the frontend:

1. **Postgres Changes** - `orders`, `status_timelines`, `delivery_coordinates`, `notifications`
   and `rider_assignments` are added to the `supabase_realtime` publication by the
   migrations. Clients subscribe with their own JWT and RLS limits what they receive.
2. **Broadcast** - this module pushes low-latency events (including throttled GPS points that
   are never written to the database) to *private* topics via the Realtime REST API.
   Topic authorization is enforced by RLS policies on `realtime.messages`.

Topics: `order:{order_id}`, `user:{user_id}`, `rider:{rider_id}`, `admin:deliveries`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

PENDING_EVENTS_KEY = "realtime_events"
ADMIN_TOPIC = "admin:deliveries"


def order_topic(order_id: Any) -> str:
    return f"order:{order_id}"


def user_topic(user_id: Any) -> str:
    return f"user:{user_id}"


def rider_topic(rider_id: Any) -> str:
    return f"rider:{rider_id}"


@dataclass
class RealtimeEvent:
    topic: str
    event: str
    # A callable is resolved after commit, so DB-generated values (ids, timestamps) are available.
    payload: dict[str, Any] | Callable[[], dict[str, Any]]

    def resolve(self) -> dict[str, Any]:
        payload = self.payload() if callable(self.payload) else self.payload
        return {"topic": self.topic, "event": self.event, "payload": jsonable_encoder(payload), "private": True}


def queue_event(session: AsyncSession, event: RealtimeEvent) -> None:
    """Queue an event to be published only after the surrounding transaction commits."""
    session.info.setdefault(PENDING_EVENTS_KEY, []).append(event)


def pop_events(session: AsyncSession) -> list[RealtimeEvent]:
    return session.info.pop(PENDING_EVENTS_KEY, [])


class RealtimePublisher:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self._client = client

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.REALTIME_ENABLED and s.SUPABASE_URL and s.SUPABASE_SERVICE_ROLE_KEY)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.settings.REALTIME_TIMEOUT_SECONDS)
        return self._client

    async def publish(self, events: list[RealtimeEvent]) -> bool:
        """Best-effort broadcast. Failures are logged, never raised: the DB commit already succeeded
        and Postgres Changes subscribers still receive the change."""
        if not events:
            return True
        if not self.enabled:
            logger.debug("Realtime disabled; dropping %d event(s)", len(events))
            return False
        key = self.settings.SUPABASE_SERVICE_ROLE_KEY
        try:
            messages = [event.resolve() for event in events]
            response = await self._get_client().post(
                f"{self.settings.SUPABASE_URL.rstrip('/')}/realtime/v1/api/broadcast",
                json={"messages": messages},
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            if response.status_code >= 400:
                logger.warning("Realtime broadcast rejected (%s): %s", response.status_code, response.text[:300])
                return False
            return True
        except Exception:  # noqa: BLE001 - realtime must never break the request
            logger.exception("Realtime broadcast failed")
            return False

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


_publisher: RealtimePublisher | None = None


def get_realtime_publisher() -> RealtimePublisher:
    global _publisher
    if _publisher is None:
        _publisher = RealtimePublisher()
    return _publisher


async def close_realtime_publisher() -> None:
    if _publisher is not None:
        await _publisher.aclose()
