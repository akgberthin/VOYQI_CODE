"""A/B tests with an uneven split; bi-weekly and monthly recurrence."""
import uuid
from datetime import datetime, timezone

from src.services import channel_policy, tracking
from tests.conftest import TENANT_HEADERS


def test_split_follows_the_percentage_and_is_stable():
    test_id = uuid.uuid4()
    contacts = [f"p{i}@x.com" for i in range(2000)]
    b = sum(1 for c in contacts if tracking.arm(c, test_id, 20) == "b")
    assert 300 < b < 500  # about 20%
    assert all(tracking.arm(c, test_id, 20) == tracking.arm(c.upper(), test_id, 20) for c in contacts[:50])


async def test_split_is_saved_and_bounded(client):
    camp = (await client.post("/api/v1/marketing/campaigns", json={"name": "C", "channel": "email"}, headers=TENANT_HEADERS)).json()
    a, b = [(await client.post("/api/v1/marketing/content", json={"campaign_id": camp["id"], "channel": "email", "body_text": "x", "subject_line": s}, headers=TENANT_HEADERS)).json() for s in ("A", "B")]
    body = {"campaign_id": camp["id"], "variant_a_draft_id": a["id"], "variant_b_draft_id": b["id"], "metric": "open_rate"}
    assert (await client.post("/api/v1/marketing/ab-tests", json={**body, "split_b_pct": 99}, headers=TENANT_HEADERS)).status_code == 422
    t = (await client.post("/api/v1/marketing/ab-tests", json={**body, "split_b_pct": 20}, headers=TENANT_HEADERS)).json()
    assert t["split_b_pct"] == 20


def test_biweekly_and_monthly_recurrence():
    jan31 = datetime(2026, 1, 31, 9, tzinfo=timezone.utc)
    assert channel_policy.next_occurrence(jan31, "biweekly") == datetime(2026, 2, 14, 9, tzinfo=timezone.utc)
    assert channel_policy.next_occurrence(jan31, "monthly") == datetime(2026, 2, 28, 9, tzinfo=timezone.utc)
    assert channel_policy.next_occurrence(datetime(2026, 12, 15, tzinfo=timezone.utc), "monthly") == datetime(2027, 1, 15, tzinfo=timezone.utc)
