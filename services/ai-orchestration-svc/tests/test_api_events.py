from tests.conftest import TENANT_HEADERS


async def test_ingest_event_routes_to_agent(client):
    resp = await client.post(
        "/api/v1/ai/events",
        json={
            "event_type": "permission.granted",
            "payload": {
                "override_id": "ov-200",
                "user_id": "user-200",
                "permission_key": "marketing.campaign.view",
                "effect": "allow",
                "is_sensitive": False,
                "granted_by": "garth",
                "ai_initiated": False,
            },
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["routed_to"] == 1


async def test_ingest_unknown_event_routes_to_nothing(client):
    resp = await client.post(
        "/api/v1/ai/events",
        json={"event_type": "some.entirely.unrouted.event", "payload": {}},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["routed_to"] == 0
