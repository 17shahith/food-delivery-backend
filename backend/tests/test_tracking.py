from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import DeliveryCoordinate
from app.models.enums import UserRole
from app.services.tracking_service import should_persist
from app.models import Rider
from app.utils.helpers import utcnow
from datetime import timedelta
from tests.conftest import auth_headers, place_order


def _loc(order_id, lat=12.9500, lng=77.6000, **extra):
    return {"order_id": order_id, "latitude": lat, "longitude": lng, **extra}


async def _count(db) -> int:
    return await db.scalar(select(func.count()).select_from(DeliveryCoordinate))


async def test_rider_location_update_and_customer_view(client, delivery, rider_user, customer, db, publisher):
    order_id = delivery["order_id"]
    response = await client.post(
        "/api/tracking/location", json=_loc(order_id, heading=90, speed=8.5, accuracy=5), headers=auth_headers(rider_user)
    )
    assert response.status_code == 200, response.text
    assert response.json()["persisted"] is True
    assert await _count(db) == 1

    tracking = await client.get(f"/api/tracking/{order_id}", headers=auth_headers(customer))
    assert tracking.status_code == 200
    body = tracking.json()
    assert body["is_live"] is True
    assert body["current_location"]["latitude"] == 12.95 and body["current_location"]["heading"] == 90
    assert body["rider"]["name"] == rider_user.name
    assert body["realtime_channel"] == f"order:{order_id}"
    assert body["destination"]["latitude"] == 12.9352

    # Location broadcast on the order and admin topics.
    assert publisher.of("location_updated", f"order:{order_id}")
    assert publisher.of("location_updated", "admin:deliveries")


async def test_gps_updates_are_throttled(client, delivery, rider_user, db, publisher, monkeypatch):
    order_id = delivery["order_id"]
    headers = auth_headers(rider_user)
    await client.post("/api/tracking/location", json=_loc(order_id), headers=headers)

    # Same spot again immediately: broadcast only, not stored.
    again = await client.post("/api/tracking/location", json=_loc(order_id), headers=headers)
    assert again.json()["persisted"] is False
    assert await _count(db) == 1
    assert len(publisher.of("location_updated", f"order:{order_id}")) == 2

    # Moving far but faster than the minimum interval is also throttled.
    quick = await client.post("/api/tracking/location", json=_loc(order_id, lat=12.96), headers=headers)
    assert quick.json()["persisted"] is False

    # With no minimum interval, a large move is stored, a tiny move is not.
    monkeypatch.setattr(get_settings(), "GPS_MIN_INTERVAL_SECONDS", 0)
    moved = await client.post("/api/tracking/location", json=_loc(order_id, lat=12.96), headers=headers)
    assert moved.json()["persisted"] is True
    jitter = await client.post("/api/tracking/location", json=_loc(order_id, lat=12.96001), headers=headers)
    assert jitter.json()["persisted"] is False
    assert await _count(db) == 2

    history = await client.get(f"/api/tracking/{order_id}/history", headers=headers)
    assert [p["latitude"] for p in history.json()["points"]] == [12.95, 12.96]


def test_should_persist_heartbeat():
    s = get_settings()
    rider = Rider(latitude=10.0, longitude=10.0, last_location_at=utcnow() - timedelta(seconds=s.GPS_HEARTBEAT_SECONDS + 1))
    assert should_persist(rider, 10.0, 10.0, utcnow()) is True
    rider.last_location_at = utcnow()
    assert should_persist(rider, 10.0, 10.0, utcnow()) is False
    rider.latitude = None
    assert should_persist(rider, 10.0, 10.0, utcnow()) is True


async def test_unassigned_rider_cannot_report_location(client, delivery, factory, db):
    intruder = await factory.user(UserRole.RIDER)
    await factory.rider(intruder)
    response = await client.post("/api/tracking/location", json=_loc(delivery["order_id"]), headers=auth_headers(intruder))
    assert response.status_code == 403 and response.json()["error"]["code"] == "NOT_ASSIGNED"
    assert await _count(db) == 0


async def test_invalid_coordinates_rejected(client, delivery, rider_user):
    headers = auth_headers(rider_user)
    for lat, lng in ((91, 0), (0, 181), (-90.1, 10)):
        response = await client.post("/api/tracking/location", json=_loc(delivery["order_id"], lat, lng), headers=headers)
        assert response.status_code == 422
    nan = await client.post(
        "/api/tracking/location",
        content=f'{{"order_id": "{delivery["order_id"]}", "latitude": NaN, "longitude": 1}}',
        headers=headers | {"Content-Type": "application/json"},
    )
    assert nan.status_code == 422


async def test_tracking_access_control(client, delivery, factory, vendor_user, admin):
    order_id = delivery["order_id"]
    stranger = await factory.user(UserRole.CUSTOMER)
    response = await client.get(f"/api/tracking/{order_id}", headers=auth_headers(stranger))
    assert response.status_code == 403
    assert (await client.get(f"/api/tracking/{order_id}/history", headers=auth_headers(stranger))).status_code == 403

    assert (await client.get(f"/api/tracking/{order_id}", headers=auth_headers(vendor_user))).status_code == 200
    assert (await client.get(f"/api/tracking/{order_id}", headers=auth_headers(admin))).status_code == 200


async def test_tracking_inactive_before_assignment_and_after_delivery(client, factory, customer, shop, delivery, rider_user):
    # Before a rider is assigned the customer cannot track.
    product = await factory.product(shop)
    pending_order = (await place_order(client, customer, [(product, 1)]))["orders"][0]["id"]
    response = await client.get(f"/api/tracking/{pending_order}", headers=auth_headers(customer))
    assert response.status_code == 409 and response.json()["error"]["code"] == "TRACKING_NOT_ACTIVE"

    # After delivery, the rider can no longer report and the customer loses live access.
    order_id = delivery["order_id"]
    for status in ("PICKED_UP", "OUT_FOR_DELIVERY", "DELIVERED"):
        await client.put(f"/api/orders/{order_id}/status", json={"status": status}, headers=auth_headers(rider_user))
    report = await client.post("/api/tracking/location", json=_loc(order_id), headers=auth_headers(rider_user))
    assert report.status_code == 409
    assert (await client.get(f"/api/tracking/{order_id}", headers=auth_headers(customer))).status_code == 409
