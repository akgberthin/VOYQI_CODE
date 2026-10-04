from src.services import crm_tools
from tests.conftest import TENANT_HEADERS


async def test_evaluate_message_endpoint(client, monkeypatch):
    monkeypatch.setattr(crm_tools, "set_message_sentiment_intent", lambda *a, **k: {})
    monkeypatch.setattr(crm_tools, "create_draft_reply", lambda *a, **k: {"id": "draft-1"})
    monkeypatch.setattr(crm_tools, "escalate_conversation", lambda *a, **k: {})

    resp = await client.post(
        "/api/v1/ai/crm/evaluate-message",
        json={"message_id": "msg-1", "conversation_id": "conv-1", "body": "This is unacceptable, I want a refund"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["sentiment"] == "negative"
    assert resp.json()["intent"] == "complaint"
    assert resp.json()["escalated"] is True
