from src.services import hub_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_hub_rebalancing_endpoint(client, monkeypatch):
    snapshot = {
        "hubs": [
            {"hub_id": "hub-1", "name": "Downtown Miami", "utilization_pct": 95.0, "latitude": 25.7743, "longitude": -80.1937},
            {"hub_id": "hub-2", "name": "Coral Gables", "utilization_pct": 40.0, "latitude": 25.7215, "longitude": -80.2684},
        ]
    }
    monkeypatch.setattr(hub_tools, "get_network_snapshot", lambda tenant_id: snapshot)
    resp = await client.post(
        "/api/v1/ai/fleet/evaluate-hub-rebalancing",
        json={"source_hub_id": "hub-1", "reason": "vehicle not staged"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["candidate_hub_id"] == "hub-2"
