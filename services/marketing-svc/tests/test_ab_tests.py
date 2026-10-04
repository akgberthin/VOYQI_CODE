from tests.conftest import TENANT_HEADERS


async def _setup_test(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "AB Test Campaign", "channel": "email"}, headers=TENANT_HEADERS)).json()
    variant_a = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign["id"], "channel": "email", "body_text": "Variant A body text."}, headers=TENANT_HEADERS)).json()
    variant_b = (await client.post("/api/v1/marketing/content", json={"campaign_id": campaign["id"], "channel": "email", "body_text": "Variant B body text."}, headers=TENANT_HEADERS)).json()
    test = (await client.post("/api/v1/marketing/ab-tests", json={"campaign_id": campaign["id"], "variant_a_draft_id": variant_a["id"], "variant_b_draft_id": variant_b["id"]}, headers=TENANT_HEADERS)).json()
    return campaign, variant_a, variant_b, test


async def test_evaluate_before_enough_data_reports_insufficient(client):
    _campaign, _a, _b, test = await _setup_test(client)
    result = (await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/evaluate", headers=TENANT_HEADERS)).json()
    assert result["status"] == "insufficient_data"
    assert result["winner_draft_id"] is None


async def test_evaluate_with_significant_difference_concludes_with_correct_winner(client):
    _campaign, _a, variant_b, test = await _setup_test(client)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "a", "sent_delta": 500, "converted_delta": 50}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "b", "sent_delta": 500, "converted_delta": 150}, headers=TENANT_HEADERS)
    result = (await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/evaluate", headers=TENANT_HEADERS)).json()
    assert result["status"] == "concluded"
    assert result["winner_draft_id"] == variant_b["id"]


async def test_regression_insufficient_data_is_re_evaluated_once_more_data_arrives(client):
    """Regression: INSUFFICIENT_DATA must be a transient status, not
    terminal. Calling evaluate before enough data exists, then again
    after recording enough for a significant result, must conclude on
    the second call — an earlier version of this engine treated any
    non-RUNNING status as final and never re-checked."""
    _campaign, _a, variant_b, test = await _setup_test(client)

    first = (await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/evaluate", headers=TENANT_HEADERS)).json()
    assert first["status"] == "insufficient_data"

    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "a", "sent_delta": 500, "converted_delta": 50}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "b", "sent_delta": 500, "converted_delta": 150}, headers=TENANT_HEADERS)

    second = (await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/evaluate", headers=TENANT_HEADERS)).json()
    assert second["status"] == "concluded"
    assert second["winner_draft_id"] == variant_b["id"]


async def test_concluded_test_is_not_re_evaluated(client):
    """CONCLUDED, unlike INSUFFICIENT_DATA, genuinely is terminal —
    further evaluate calls must be no-ops."""
    _campaign, _a, variant_b, test = await _setup_test(client)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "a", "sent_delta": 500, "converted_delta": 50}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "b", "sent_delta": 500, "converted_delta": 150}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/evaluate", headers=TENANT_HEADERS)

    # Recording wildly different results after conclusion must not flip the winner.
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "a", "sent_delta": 5000, "converted_delta": 4000}, headers=TENANT_HEADERS)
    still = (await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/evaluate", headers=TENANT_HEADERS)).json()
    assert still["status"] == "concluded"
    assert still["winner_draft_id"] == variant_b["id"]


async def test_close_result_with_enough_data_stays_insufficient(client):
    """A small, statistically insignificant gap must not be forced
    into a winner just because both arms cleared the sample floor."""
    _campaign, _a, _b, test = await _setup_test(client)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "a", "sent_delta": 100, "converted_delta": 20}, headers=TENANT_HEADERS)
    await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/record-result", json={"variant": "b", "sent_delta": 100, "converted_delta": 22}, headers=TENANT_HEADERS)
    result = (await client.post(f"/api/v1/marketing/ab-tests/{test['id']}/evaluate", headers=TENANT_HEADERS)).json()
    assert result["status"] == "insufficient_data"
    assert result["winner_draft_id"] is None
