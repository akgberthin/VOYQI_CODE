"""Attributes synced in from other services (the Member Experience Agent)
reach marketing contacts and start churn / upgrade journeys."""
from tests.conftest import TENANT_HEADERS

M = "/api/v1/marketing"
H = TENANT_HEADERS
AGENT = {**H, "X-AI-Initiated": "member_experience_agent"}


async def _journey(client, trigger):
    j = (await client.post(f"{M}/journeys", json={"name": trigger, "trigger_event": trigger,
                                                  "steps": [{"delay_hours": 0, "channel": "email", "subject": "Hi", "body": "Hello {first_name}"}]}, headers=H)).json()
    await client.patch(f"{M}/journeys/{j['id']}", json={"status": "active"}, headers=H)
    return j


async def test_sync_updates_known_contacts_and_adds_new_ones(client):
    seg = (await client.post(f"{M}/audience/segments", json={"name": "Imported"}, headers=H)).json()
    await client.post(f"{M}/audience/segments/{seg['id']}/members", json={"members": [
        {"name": "Ava", "email": "ava@x.com", "attributes": {"member_profile_id": "m-1", "city": "Miami"}}]}, headers=H)
    r = (await client.post(f"{M}/audience/contacts/sync", json={"contacts": [
        {"member_profile_id": "m-1", "attributes": {"churn_risk": 20}},
        {"member_profile_id": "m-2", "email": "noah@x.com", "name": "Noah", "attributes": {"churn_risk": 30}},
        {"member_profile_id": "m-3", "attributes": {"churn_risk": 90}},  # no way to reach them: skipped
    ]}, headers=AGENT)).json()
    assert r == {"updated": 1, "added": 1, "journeys_started": 0}
    est = (await client.post(f"{M}/audience/estimate", json={"filter_criteria": {"churn_risk": {"max": 25}, "city": "Miami"}}, headers=H)).json()
    assert est["total"] == 1
    segs = {s["name"]: s for s in (await client.get(f"{M}/audience/segments", headers=H)).json()}
    assert segs["Members (synced)"]["member_count"] == 1


async def test_crossing_into_high_churn_risk_starts_the_journey_once(client):
    j = await _journey(client, "member.churn_risk_high")
    body = {"contacts": [{"member_profile_id": "m-9", "email": "mia@x.com", "attributes": {"churn_risk": 40}}]}
    assert (await client.post(f"{M}/audience/contacts/sync", json=body, headers=AGENT)).json()["journeys_started"] == 0
    body["contacts"][0]["attributes"]["churn_risk"] = 72
    assert (await client.post(f"{M}/audience/contacts/sync", json=body, headers=AGENT)).json()["journeys_started"] == 1
    body["contacts"][0]["attributes"]["churn_risk"] = 80  # still high: not a new crossing
    assert (await client.post(f"{M}/audience/contacts/sync", json=body, headers=AGENT)).json()["journeys_started"] == 0
    enrolled = (await client.get(f"{M}/journeys/{j['id']}/enrollments", headers=H)).json()
    assert [e["email"] for e in enrolled] == ["mia@x.com"]


async def test_becoming_upgrade_ready_starts_the_upgrade_journey(client):
    j = await _journey(client, "member.upgrade_ready")
    r = (await client.post(f"{M}/audience/contacts/sync", json={"contacts": [
        {"email": "leo@x.com", "attributes": {"upgrade_ready": True, "recommended_class": "luxury"}}]}, headers=AGENT)).json()
    assert r["journeys_started"] == 1
    assert len((await client.get(f"{M}/journeys/{j['id']}/enrollments", headers=H)).json()) == 1
