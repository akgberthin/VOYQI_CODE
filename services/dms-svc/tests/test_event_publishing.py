"""Compliance-relevant DMS state changes publish the events the
Compliance/Audit Agent in ai-orchestration-svc is routed on."""
import uuid

import pytest

from tests.conftest import TENANT_HEADERS
from tests.test_franchise import _rooftop
from tests.test_lending import _setup_app


@pytest.fixture
def published(monkeypatch):
    events = []

    async def _spy(topic, event_type, tenant_id, payload):
        events.append({"topic": topic, "event_type": event_type, "tenant_id": tenant_id, "payload": payload})

    for module in ("lending", "fi", "franchise"):
        monkeypatch.setattr(f"src.api.{module}.publish_event", _spy)
    return events


async def test_ofac_match_publishes_event(client, published):
    app = await _setup_app(client)
    await client.post(
        f"/api/v1/dms/lending/credit-applications/{app['id']}/pull-bureau",
        json={"bureau": "experian", "fico": 720, "ofac_match": True},
        headers=TENANT_HEADERS,
    )

    [event] = published
    assert event["event_type"] == "credit.ofac_match_detected"
    assert event["payload"] == {"credit_application_id": app["id"], "bureau": "experian"}


async def test_clean_bureau_pull_publishes_nothing(client, published):
    app = await _setup_app(client)
    await client.post(
        f"/api/v1/dms/lending/credit-applications/{app['id']}/pull-bureau",
        json={"bureau": "experian", "fico": 720, "ofac_match": False},
        headers=TENANT_HEADERS,
    )
    assert published == []


async def test_red_flag_override_publishes_event(client, published):
    check = (
        await client.post(
            "/api/v1/dms/fi/red-flag-checks", json={"deal_id": str(uuid.uuid4()), "flags": ["address mismatch"]}, headers=TENANT_HEADERS
        )
    ).json()
    await client.post(
        f"/api/v1/dms/fi/red-flag-checks/{check['id']}/override",
        json={"overridden_by": "fi-manager-1", "reason": "verified in person"},
        headers=TENANT_HEADERS,
    )

    [event] = published
    assert event["event_type"] == "red_flag.check_overridden"
    assert event["payload"]["flags_detected"] == ["address mismatch"]
    assert event["payload"]["override_reason"] == "verified in person"


async def test_rejected_red_flag_override_publishes_nothing(client, published):
    check = (
        await client.post(
            "/api/v1/dms/fi/red-flag-checks", json={"deal_id": str(uuid.uuid4()), "flags": ["address mismatch"]}, headers=TENANT_HEADERS
        )
    ).json()
    resp = await client.post(
        f"/api/v1/dms/fi/red-flag-checks/{check['id']}/override", json={"overridden_by": "fi-manager-1", "reason": ""}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 422
    assert published == []


async def test_franchise_non_compliance_publishes_once(client, published):
    rooftop_id = await _rooftop(client)
    await client.post(
        "/api/v1/dms/franchise", json={"rooftop_id": rooftop_id, "status": "non_compliant", "score": 61, "notes": "signage lapsed"}, headers=TENANT_HEADERS
    )
    await client.post("/api/v1/dms/franchise/evaluate-remediation", headers=TENANT_HEADERS)
    await client.post("/api/v1/dms/franchise/evaluate-remediation", headers=TENANT_HEADERS)

    [event] = published
    assert event["event_type"] == "franchise.facility_non_compliant"
    assert event["payload"]["rooftop_id"] == rooftop_id
    assert event["payload"]["notes"] == "signage lapsed"
