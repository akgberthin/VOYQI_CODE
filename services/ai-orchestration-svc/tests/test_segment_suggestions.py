"""AI-suggested segments: proposals from the contact attributes marketing
holds, kept only when they reach someone and aren't already a segment."""
from unittest.mock import MagicMock

from src.services import segment_suggestions

TENANT = "voyqi-holdings"
ATTRS = {"churn_risk": ["20", "60", "75"], "tenure_months": ["2", "14", "30"], "city": ["Austin", "Miami"], "upgrade_ready": ["true"]}


class _NoClaude:
    is_configured = False


def _marketing(existing=(), totals=None):
    calls = []

    def fake(tenant_id, method, path, body=None):
        calls.append((method, path, body))
        if path == "/audience/attributes":
            return ATTRS
        if path == "/audience/segments":
            return list(existing)
        crit = body["filter_criteria"]
        return {"total": (totals or {}).get(str(crit), 3), "of": 10, "reachable": {"email": 3, "sms": 1, "push": 0, "voice": 0}}

    return fake, calls


def test_rules_use_only_the_attributes_that_exist():
    names = [s["name"] for s in segment_suggestions.rule_suggestions(ATTRS)]
    assert names[:3] == ["At churn risk", "Ready to upgrade", "Loyal and happy"] and "Austin members" in names
    assert segment_suggestions.rule_suggestions({}) == []


async def test_suggestions_skip_existing_and_empty_audiences(db_session, monkeypatch):
    existing = [{"name": "Churn", "filter_criteria": {"churn_risk": {"min": 50}}}]
    fake, _ = _marketing(existing, totals={str({"upgrade_ready": ["true", "True", "yes", "1"]}): 0})
    monkeypatch.setattr(segment_suggestions, "_marketing", fake)
    out = await segment_suggestions.suggest(db_session, TENANT, client=_NoClaude())
    names = [s["name"] for s in out["suggestions"]]
    assert out["source"] == "rules" and "At churn risk" not in names and "Ready to upgrade" not in names
    assert "Loyal and happy" in names and out["suggestions"][0]["estimate"]["total"] == 3


async def test_claude_suggestions_are_used_and_checked(db_session, monkeypatch):
    fake, calls = _marketing()
    monkeypatch.setattr(segment_suggestions, "_marketing", fake)
    client = MagicMock(is_configured=True)
    client.complete.return_value = 'Here: {"suggestions": [{"name": "Miami newcomers", "why": "New in a big market", "filter_criteria": {"city": "Miami", "tenure_months": {"max": 3}}, "channel": "sms"}, {"bad": 1}]}'
    out = await segment_suggestions.suggest(db_session, TENANT, client=client)
    assert out["source"] == "claude" and [s["name"] for s in out["suggestions"]] == ["Miami newcomers"]
    assert out["suggestions"][0]["channel"] == "sms" and ("POST", "/audience/estimate", {"filter_criteria": {"city": "Miami", "tenure_months": {"max": 3}}}) in calls
