from tests.conftest import TENANT_HEADERS


async def test_create_reservation_publishes_rental_booked(client, monkeypatch):
    """rental.booked is what routes a new booking to the Pricing
    Intelligence Agent — its payload carries the three fields
    pricing_intelligence_agent.handle_event reads."""
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append((topic, event_type, tenant_id, payload))

    monkeypatch.setattr("src.api.reservations.publish_event", _spy)
    resp = await client.post(
        "/api/v1/rental/reservations",
        json={"hub_id": "hub-1", "rental_class": "urban", "customer_name": "C", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": 60},
        headers=TENANT_HEADERS,
    )

    [(topic, event_type, tenant_id, payload)] = events
    assert topic == "rental.reservations"
    assert event_type == "rental.booked"
    assert payload["reservation_id"] == resp.json()["id"]
    assert payload["hub_id"] == "hub-1"
    assert payload["rental_class"] == "urban"
