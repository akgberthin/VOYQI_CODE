"""Social autopilot: plans a week of posts per account through the agent
and the guardrail; suggest mode waits for people, autopublish schedules
clean posts; flagged copy is always held."""
import base64
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core import integrations, kafka_events
from src.services import ai_drafting, autopilot, media_engine, scheduler
from tests.conftest import TENANT_HEADERS

M = "/api/v1/marketing"
H = TENANT_HEADERS
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def agent(monkeypatch):
    state = {"calls": [], "body": "Every class, every hub. #VOYQI", "skipped": None}

    async def fake_compose(tenant_id, payload):
        state["calls"].append(payload)
        if state["skipped"]:
            return {"variants": [], "skipped_reason": state["skipped"]}
        return {"variants": [{"tone": payload["tones"][0], "source": "fallback", "subject": "", "body": state["body"]}], "skipped_reason": None}

    async def no_openai(tenant_id, provider):
        return None

    monkeypatch.setattr(ai_drafting, "compose", fake_compose)
    monkeypatch.setattr(integrations, "get", no_openai)
    return state


@pytest.fixture
def events(monkeypatch):
    sent = []

    async def record(topic, event_type, tenant_id, payload):
        sent.append(event_type)

    monkeypatch.setattr(kafka_events, "publish_event", record)
    monkeypatch.setattr(autopilot, "publish_event", record)
    return sent


async def _setup(client, mode="suggest", per_week=3, accounts=("instagram",), themes="Miami hub opening\nWeekend luxury"):
    ids = [(await client.post(f"{M}/social/accounts", json={"platform": p, "handle": "@voyqi"}, headers=H)).json()["id"] for p in accounts]
    cfg = await client.put(f"{M}/autopilot", json={"enabled": True, "mode": mode, "posts_per_week": per_week, "themes": themes, "mobility_class": "luxury", "tone": "exclusive", "account_ids": ids}, headers=H)
    assert cfg.status_code == 200
    return ids


async def test_config_defaults_and_validation(client):
    cfg = (await client.get(f"{M}/autopilot", headers=H)).json()
    assert (cfg["enabled"], cfg["mode"], cfg["posts_per_week"]) == (False, "suggest", 3)
    assert (await client.put(f"{M}/autopilot", json={"mode": "yolo"}, headers=H)).status_code == 422
    assert (await client.put(f"{M}/autopilot", json={"posts_per_week": 50}, headers=H)).status_code == 422


async def test_suggest_mode_plans_the_week_and_waits_for_people(client, agent):
    await _setup(client, per_week=3, accounts=("instagram", "facebook"))
    assert (await client.post(f"{M}/autopilot/plan-now", headers=H)).json() == {"planned": 6}
    slots = (await client.get(f"{M}/autopilot/slots", headers=H)).json()
    assert len(slots) == 6 and {s["status"] for s in slots} == {"proposed"}
    times = sorted({s["planned_at"] for s in slots})
    assert len(times) == 3 and all(t.endswith("16:00:00Z") or "16:00:00" in t for t in times)
    assert agent["calls"][0]["tones"] == ["exclusive"] and agent["calls"][0]["mobility_class"] == "luxury"
    assert {c["context"] for c in agent["calls"]} == {"Miami hub opening", "Weekend luxury"}
    assert (await client.get(f"{M}/social/posts", headers=H)).json() == []
    assert (await client.post(f"{M}/autopilot/plan-now", headers=H)).json() == {"planned": 0}  # no duplicates


async def test_approving_schedules_and_rejecting_discards(client, agent):
    await _setup(client, per_week=2)
    await client.post(f"{M}/autopilot/plan-now", headers=H)
    first, second = (await client.get(f"{M}/autopilot/slots", headers=H)).json()
    approved = (await client.post(f"{M}/autopilot/slots/{first['id']}/approve", headers={**H, "X-User-Id": "mm"}, )).json()
    assert approved["status"] == "scheduled" and approved["social_post_id"] and approved["decided_by"] == "mm"
    [post] = (await client.get(f"{M}/social/posts", headers=H)).json()
    assert post["scheduled_at"].startswith(first["planned_at"][:16])
    rejected = (await client.post(f"{M}/autopilot/slots/{second['id']}/reject", headers=H)).json()
    assert rejected["status"] == "rejected"
    assert (await client.get(f"{M}/content/{second['content_draft_id']}", headers=H)).json()["status"] == "rejected"
    assert (await client.post(f"{M}/autopilot/slots/{second['id']}/approve", headers=H)).status_code == 409


async def test_autopublish_schedules_clean_posts_but_holds_flagged_ones(client, agent, events):
    await _setup(client, mode="autopublish", per_week=1)
    await client.post(f"{M}/autopilot/plan-now", headers=H)
    [clean] = (await client.get(f"{M}/autopilot/slots", headers=H)).json()
    assert clean["status"] == "scheduled"
    agent["body"] = "Guaranteed savings, risk-free!"
    await _setup(client, mode="autopublish", per_week=1, accounts=("linkedin",))
    await client.post(f"{M}/autopilot/plan-now", headers=H)
    held = [s for s in (await client.get(f"{M}/autopilot/slots", headers=H)).json() if s["id"] != clean["id"]][0]
    assert held["status"] == "held" and held["social_post_id"] is None
    done = (await client.post(f"{M}/autopilot/slots/{held['id']}/approve", headers=H)).json()
    assert done["status"] == "scheduled" and "content_draft.approved_with_violations" in events


async def test_disabled_or_paused_agent_plans_nothing(client, agent):
    assert (await client.post(f"{M}/autopilot/plan-now", headers=H)).json() == {"planned": 0}  # disabled by default
    await _setup(client)
    agent["skipped"] = "paused"
    assert (await client.post(f"{M}/autopilot/plan-now", headers=H)).json() == {"planned": 0}


async def test_images_come_from_openai_when_configured(client, agent, monkeypatch):
    async def with_openai(tenant_id, provider):
        return {"api_key": "sk-x"} if provider == "openai" else None

    async def fake_image(config, prompt):
        assert "Miami hub opening" in prompt
        return PNG

    monkeypatch.setattr(integrations, "get", with_openai)
    monkeypatch.setattr(media_engine, "openai_image", fake_image)
    await _setup(client, per_week=1, themes="Miami hub opening")
    await client.post(f"{M}/autopilot/plan-now", headers=H)
    [slot] = (await client.get(f"{M}/autopilot/slots", headers=H)).json()
    assert slot["media_asset_id"] and slot["image_url"].endswith(slot["media_asset_id"])


async def test_scheduler_plans_enabled_tenants_and_not_again_for_12_hours(client, db_engine, agent):
    await _setup(client, per_week=1)
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    await scheduler.run_once(factory)
    assert len((await client.get(f"{M}/autopilot/slots", headers=H)).json()) == 1
    async with factory() as db:
        assert await autopilot.due_configs(db) == []
        assert await autopilot.due_configs(db, now=datetime.now(timezone.utc) + timedelta(hours=13)) == [H["X-Tenant-Id"]]
