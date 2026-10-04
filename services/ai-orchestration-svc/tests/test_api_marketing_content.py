from src.services import marketing_tools
from tests.conftest import TENANT_HEADERS


async def test_generate_content_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        marketing_tools,
        "create_content_draft",
        lambda *a, **k: {"id": "draft-1", "guardrail_violations": []},
    )
    resp = await client.post(
        "/api/v1/ai/marketing/generate-content",
        json={
            "campaign_id": "camp-1", "campaign_name": "Summer Push",
            "channel": "email", "brief": "highlight the Executive class",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["draft"] == {"id": "draft-1", "guardrail_violations": []}
