from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.realtime_service import RealtimeEvent, RealtimePublisher, pop_events, queue_event


class BaseService:
    """Holds the request's DB session and publishes queued realtime events after commit."""

    def __init__(self, session: AsyncSession, publisher: RealtimePublisher | None = None):
        self.session = session
        self.publisher = publisher

    def queue_event(self, topic: str, event: str, payload: Any) -> None:
        queue_event(self.session, RealtimeEvent(topic=topic, event=event, payload=payload))

    async def commit(self) -> None:
        try:
            await self.session.commit()
        except Exception:
            pop_events(self.session)
            raise
        events = pop_events(self.session)
        if events and self.publisher is not None:
            await self.publisher.publish(events)

    async def rollback(self) -> None:
        pop_events(self.session)
        await self.session.rollback()
