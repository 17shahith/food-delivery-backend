"""Supabase Realtime integration: events emitted by workflows and the REST broadcast client."""

import json

import httpx

from app.core.config import Settings
from app.services.realtime_service import RealtimeEvent, RealtimePublisher
from tests.conftest import auth_headers, place_order, set_status


async def test_status_changes_publish_after_commit(client, delivery, rider_user, customer, publisher):
    order_id = delivery["order_id"]
    await set_status(client, rider_user, order_id, "PICKED_UP")

    status_events = publisher.of("status_changed", f"order:{order_id}")
    assert [e["payload"]["status"] for e in status_events] == [
        "VENDOR_ACCEPTED", "PREPARING", "READY_FOR_PICKUP", "RIDER_ASSIGNED", "PICKED_UP"
    ]
    assert status_events[-1]["payload"]["updated_by"] == str(rider_user.id)
    assert all(e["private"] is True for e in publisher.events)

    rider_assigned = publisher.of("rider_assigned", f"order:{order_id}")
    assert rider_assigned and rider_assigned[0]["payload"]["rider"]["name"] == rider_user.name

    # Notifications are pushed to the recipient's private user topic with DB-generated fields.
    customer_events = publisher.of("notification", f"user:{customer.id}")
    assert customer_events and customer_events[-1]["payload"]["id"]
    assert customer_events[-1]["payload"]["created_at"]


async def test_assignment_offer_published_to_rider_topic(client, factory, customer, vendor_user, shop, rider, publisher):
    from tests.conftest import advance_to_ready

    product = await factory.product(shop)
    order_id = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    await advance_to_ready(client, vendor_user, order_id)
    await client.post("/api/riders/assign", json={"order_id": order_id, "rider_id": str(rider.id)}, headers=auth_headers(vendor_user))
    [offer] = publisher.of("assignment_offered", f"rider:{rider.id}")
    assert offer["payload"]["order_id"] == order_id
    assert offer["payload"]["pickup"]["shop_name"] == shop.shop_name


async def test_failed_operation_publishes_nothing(client, factory, customer, shop, publisher):
    product = await factory.product(shop, availability=False)
    response = await client.post(
        "/api/orders",
        json={"items": [{"product_id": str(product.id), "quantity": 1}], "delivery_address": "Somewhere 1"},
        headers=auth_headers(customer),
    )
    assert response.status_code == 409
    assert publisher.events == []


def _settings(**overrides) -> Settings:
    base = {"SUPABASE_URL": "https://proj.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "service-key", "REALTIME_ENABLED": True}
    return Settings(_env_file=None, **(base | overrides))


async def test_publisher_posts_private_broadcast_to_supabase():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(202)

    publisher = RealtimePublisher(_settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    ok = await publisher.publish([RealtimeEvent("order:1", "status_changed", lambda: {"status": "PICKED_UP"})])
    assert ok is True
    assert captured["url"] == "https://proj.supabase.co/realtime/v1/api/broadcast"
    assert captured["headers"]["apikey"] == "service-key"
    assert captured["headers"]["authorization"] == "Bearer service-key"
    assert captured["body"] == {
        "messages": [{"topic": "order:1", "event": "status_changed", "payload": {"status": "PICKED_UP"}, "private": True}]
    }


async def test_publisher_failures_never_raise():
    def boom(request):
        raise httpx.ConnectError("down")

    publisher = RealtimePublisher(_settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    assert await publisher.publish([RealtimeEvent("t", "e", {})]) is False

    rejected = RealtimePublisher(_settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    assert await rejected.publish([RealtimeEvent("t", "e", {})]) is False


async def test_publisher_disabled_without_configuration():
    publisher = RealtimePublisher(_settings(SUPABASE_SERVICE_ROLE_KEY=""))
    assert publisher.enabled is False
    assert await publisher.publish([RealtimeEvent("t", "e", {})]) is False
