"""AI studio copy variants: one per tone, from Claude when configured and
from guardrail-safe templates otherwise; logged; respects the kill switch."""
import pytest

from src.models import Agent, AgentStatus
from src.services import marketing_content_agent as mca
from tests.conftest import TENANT_HEADERS

BANNED = ("guaranteed", "risk-free", "no risk", "cure", "free money")


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    @property
    def is_configured(self):
        return True

    def complete(self, system_prompt, user_message, max_tokens=None):
        self.calls.append(user_message)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.parametrize("channel", ["email", "sms", "push", "social", "voice"])
async def test_fallback_variants_differ_by_tone_and_stay_on_brand(db_session, channel):
    out = await mca.compose_variants(db_session, "t1", channel, "luxury", "upgrade", ["premium", "personal", "urgent"], "Miami hub opening")
    bodies = [v["body"] for v in out["variants"]]
    assert [v["tone"] for v in out["variants"]] == ["premium", "personal", "urgent"] and len(set(bodies)) >= (1 if channel in ("sms", "push") else 3)
    assert all(v["source"] == "fallback" for v in out["variants"])
    assert not any(b in (v["subject"] + v["body"]).lower() for v in out["variants"] for b in BANNED)
    if channel == "sms":
        assert all("Reply STOP to opt out." in v["body"] and len(v["body"]) <= 160 for v in out["variants"])
    if channel == "email":
        assert "$1,499" in out["variants"][0]["body"] and out["variants"][0]["subject"]


async def test_claude_variants_are_parsed_and_bad_replies_fall_back(db_session):
    client = FakeClient(['Here you go: {"subject": "Arrive in Luxury", "body": "The Luxury Class awaits."}', "not json", RuntimeError("overloaded")])
    out = await mca.compose_variants(db_session, "t1", "email", "luxury", "acquisition", ["premium", "personal", "urgent"], "", client=client)
    assert out["variants"][0] == {"tone": "premium", "source": "claude", "subject": "Arrive in Luxury", "body": "The Luxury Class awaits."}
    assert [v["source"] for v in out["variants"][1:]] == ["fallback", "fallback"]
    assert "Tone: personal" in client.calls[1] and "$1,499/month" in client.calls[0]


async def test_compose_api_and_kill_switch(client, db_session):
    resp = await client.post("/api/v1/ai/marketing/compose", json={"channel": "social", "mobility_class": "urban", "objective": "referral"}, headers=TENANT_HEADERS)
    assert resp.status_code == 200 and len(resp.json()["variants"]) == 3
    db_session.add(Agent(tenant_id=TENANT_HEADERS["X-Tenant-Id"], key=mca.AGENT_KEY, display_name="Marketing", status=AgentStatus.PAUSED))
    await db_session.commit()
    paused = (await client.post("/api/v1/ai/marketing/compose", json={"channel": "email"}, headers=TENANT_HEADERS)).json()
    assert paused == {"variants": [], "skipped_reason": "paused"}


def test_the_compose_prompt_is_versionable():
    from src.core import prompts
    assert "marketing_content_agent.COMPOSE_SYSTEM_PROMPT" in prompts.registry()


async def test_social_fallback_carries_the_theme_so_planned_posts_differ(db_session):
    a = await mca.compose_variants(db_session, "t1", "social", "luxury", "acquisition", ["exclusive"], "Brickell hub opening")
    b = await mca.compose_variants(db_session, "t1", "social", "luxury", "acquisition", ["exclusive"], "Weekend luxury availability")
    assert a["variants"][0]["body"].startswith("Brickell hub opening") and a["variants"][0]["body"] != b["variants"][0]["body"]
    assert len(a["variants"][0]["body"]) <= 260
