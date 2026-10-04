from tests.conftest import TENANT_HEADERS


async def test_record_snapshot_and_get_summary(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": "email"}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/marketing/analytics/snapshots", json={
        "campaign_id": campaign["id"], "channel": "email", "sent_count": 1000, "open_count": 400, "click_count": 100, "conversion_count": 20, "revenue_usd": 5000,
    }, headers=TENANT_HEADERS)
    summary = (await client.get(f"/api/v1/marketing/analytics/campaigns/{campaign['id']}/summary", headers=TENANT_HEADERS)).json()
    assert summary["total_sent"] == 1000
    assert summary["open_rate"] == 0.4
    assert summary["click_rate"] == 0.1
    assert summary["conversion_rate"] == 0.02
    assert summary["total_revenue_usd"] == 5000


async def test_summary_aggregates_multiple_snapshots(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Test", "channel": "email"}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/marketing/analytics/snapshots", json={"campaign_id": campaign["id"], "channel": "email", "sent_count": 500, "open_count": 200, "click_count": 50, "conversion_count": 10, "revenue_usd": 1000}, headers=TENANT_HEADERS)
    await client.post("/api/v1/marketing/analytics/snapshots", json={"campaign_id": campaign["id"], "channel": "email", "sent_count": 500, "open_count": 250, "click_count": 60, "conversion_count": 15, "revenue_usd": 1500}, headers=TENANT_HEADERS)
    summary = (await client.get(f"/api/v1/marketing/analytics/campaigns/{campaign['id']}/summary", headers=TENANT_HEADERS)).json()
    assert summary["total_sent"] == 1000
    assert summary["total_revenue_usd"] == 2500


async def test_summary_with_no_snapshots_returns_zero_rates(client):
    campaign = (await client.post("/api/v1/marketing/campaigns", json={"name": "Empty", "channel": "email"}, headers=TENANT_HEADERS)).json()
    summary = (await client.get(f"/api/v1/marketing/analytics/campaigns/{campaign['id']}/summary", headers=TENANT_HEADERS)).json()
    assert summary["open_rate"] == 0.0
    assert summary["total_sent"] == 0


async def test_media_generation_request_lifecycle(client):
    request = (await client.post("/api/v1/marketing/media-generation", json={"prompt": "Luxury SUV at sunset over Miami skyline", "media_type": "image"}, headers=TENANT_HEADERS)).json()
    assert request["status"] == "pending"

    generating = (await client.post(f"/api/v1/marketing/media-generation/{request['id']}/generating", params={"provider": "example-mcp-image-gen"}, headers=TENANT_HEADERS)).json()
    assert generating["status"] == "generating"
    assert generating["provider"] == "example-mcp-image-gen"

    completed = (await client.post(f"/api/v1/marketing/media-generation/{request['id']}/complete", json={"result_url": "https://cdn.example.com/generated/abc123.jpg"}, headers=TENANT_HEADERS)).json()
    assert completed["status"] == "completed"
    assert completed["result_url"] == "https://cdn.example.com/generated/abc123.jpg"


async def test_media_generation_failure(client):
    request = (await client.post("/api/v1/marketing/media-generation", json={"prompt": "Test", "media_type": "video"}, headers=TENANT_HEADERS)).json()
    failed = (await client.post(f"/api/v1/marketing/media-generation/{request['id']}/fail", json={"error_detail": "provider timeout"}, headers=TENANT_HEADERS)).json()
    assert failed["status"] == "failed"
    assert failed["error_detail"] == "provider timeout"


async def test_filter_media_requests_by_status(client):
    await client.post("/api/v1/marketing/media-generation", json={"prompt": "A", "media_type": "image"}, headers=TENANT_HEADERS)
    req_b = (await client.post("/api/v1/marketing/media-generation", json={"prompt": "B", "media_type": "image"}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/marketing/media-generation/{req_b['id']}/complete", json={"result_url": "https://cdn.example.com/b.jpg"}, headers=TENANT_HEADERS)

    pending = (await client.get("/api/v1/marketing/media-generation", params={"status": "pending"}, headers=TENANT_HEADERS)).json()
    completed = (await client.get("/api/v1/marketing/media-generation", params={"status": "completed"}, headers=TENANT_HEADERS)).json()
    assert len(pending) == 1
    assert len(completed) == 1
