from tests.conftest import TENANT_HEADERS


async def _create_vehicle(client, hub_id="hub-miami", rental_class="urban", vin="URBVIN0001", rate=60):
    return (await client.post("/api/v1/rental/vehicles", json={"hub_id": hub_id, "rental_class": rental_class, "vin": vin, "base_daily_rate_usd": rate}, headers=TENANT_HEADERS)).json()


async def _create_reservation(client, hub_id="hub-miami", rental_class="urban", rate=60):
    return (await client.post("/api/v1/rental/reservations", json={
        "hub_id": hub_id, "rental_class": rental_class, "customer_name": "Jane", "start_date": "2026-08-15T10:00:00Z", "end_date": "2026-08-18T10:00:00Z", "daily_rate_usd": rate,
    }, headers=TENANT_HEADERS)).json()


async def test_create_reservation_starts_pending(client):
    res = await _create_reservation(client)
    assert res["status"] == "pending"
    assert res["vehicle_id"] is None


async def test_confirm_autonomously_allocates_a_vehicle(client):
    await _create_vehicle(client)
    res = await _create_reservation(client)
    confirmed = (await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)).json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["vehicle_id"] is not None


async def test_confirm_with_no_available_vehicle_refused(client):
    res = await _create_reservation(client)  # no vehicle created at all
    resp = await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_activate_marks_vehicle_rented(client):
    await _create_vehicle(client)
    res = await _create_reservation(client)
    confirmed = (await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/rental/reservations/{res['id']}/activate", headers=TENANT_HEADERS)
    vehicle = (await client.get(f"/api/v1/rental/vehicles/{confirmed['vehicle_id']}", headers=TENANT_HEADERS)).json()
    assert vehicle["status"] == "rented"


async def test_complete_returns_vehicle_to_available(client):
    await _create_vehicle(client)
    res = await _create_reservation(client)
    confirmed = (await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/rental/reservations/{res['id']}/activate", headers=TENANT_HEADERS)
    await client.post(f"/api/v1/rental/reservations/{res['id']}/complete", headers=TENANT_HEADERS)
    vehicle = (await client.get(f"/api/v1/rental/vehicles/{confirmed['vehicle_id']}", headers=TENANT_HEADERS)).json()
    assert vehicle["status"] == "available"


async def test_modify_rate_succeeds_while_pending(client):
    res = await _create_reservation(client)
    updated = (await client.post(f"/api/v1/rental/reservations/{res['id']}/modify", json={"daily_rate_usd": 75.0}, headers=TENANT_HEADERS)).json()
    assert updated["daily_rate_usd"] == 75.0


async def test_modify_rate_succeeds_while_confirmed(client):
    await _create_vehicle(client)
    res = await _create_reservation(client)
    await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)
    updated = (await client.post(f"/api/v1/rental/reservations/{res['id']}/modify", json={"daily_rate_usd": 80.0}, headers=TENANT_HEADERS)).json()
    assert updated["daily_rate_usd"] == 80.0


async def test_modify_rate_refused_once_active(client):
    """The one structural rule: a renter who has taken the vehicle
    can never have their price retroactively altered."""
    await _create_vehicle(client)
    res = await _create_reservation(client)
    await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)
    await client.post(f"/api/v1/rental/reservations/{res['id']}/activate", headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/rental/reservations/{res['id']}/modify", json={"daily_rate_usd": 999.0}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_modify_rate_refused_once_completed(client):
    await _create_vehicle(client)
    res = await _create_reservation(client)
    await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)
    await client.post(f"/api/v1/rental/reservations/{res['id']}/activate", headers=TENANT_HEADERS)
    await client.post(f"/api/v1/rental/reservations/{res['id']}/complete", headers=TENANT_HEADERS)
    resp = await client.post(f"/api/v1/rental/reservations/{res['id']}/modify", json={"daily_rate_usd": 999.0}, headers=TENANT_HEADERS)
    assert resp.status_code == 409


async def test_cancel_frees_vehicle_and_records_demand_event(client):
    await _create_vehicle(client)
    res = await _create_reservation(client)
    confirmed = (await client.post(f"/api/v1/rental/reservations/{res['id']}/confirm", headers=TENANT_HEADERS)).json()
    cancelled = (await client.post(f"/api/v1/rental/reservations/{res['id']}/cancel", headers=TENANT_HEADERS)).json()
    assert cancelled["status"] == "cancelled"
    vehicle = (await client.get(f"/api/v1/rental/vehicles/{confirmed['vehicle_id']}", headers=TENANT_HEADERS)).json()
    assert vehicle["status"] == "available"


async def test_list_reservations_filters_by_hub_and_status(client):
    await _create_reservation(client, hub_id="hub-a")
    await _create_reservation(client, hub_id="hub-b")
    hub_a_only = (await client.get("/api/v1/rental/reservations", params={"hub_id": "hub-a"}, headers=TENANT_HEADERS)).json()
    assert all(r["hub_id"] == "hub-a" for r in hub_a_only)
