from tests.conftest import TENANT_HEADERS


async def test_create_and_list_payment_gateway_setting(client):
    resp = await client.post("/api/v1/rbac/settings", json={
        "category": "payment_gateway", "key": "stripe", "value": {"public_key": "pk_test_123", "enabled": True},
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["value"]["enabled"] is True

    listed = (await client.get("/api/v1/rbac/settings", params={"category": "payment_gateway"}, headers=TENANT_HEADERS)).json()
    assert any(s["key"] == "stripe" for s in listed)


async def test_upsert_updates_existing_setting_rather_than_duplicating(client):
    await client.post("/api/v1/rbac/settings", json={"category": "branding", "key": "primary_color", "value": {"hex": "#c9a84c"}}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/settings", json={"category": "branding", "key": "primary_color", "value": {"hex": "#000000"}}, headers=TENANT_HEADERS)
    listed = (await client.get("/api/v1/rbac/settings", params={"category": "branding"}, headers=TENANT_HEADERS)).json()
    matches = [s for s in listed if s["key"] == "primary_color"]
    assert len(matches) == 1
    assert matches[0]["value"]["hex"] == "#000000"


async def test_feature_flag_setting(client):
    resp = await client.post("/api/v1/rbac/settings", json={"category": "feature_flag", "key": "new_deal_desk_ui", "value": {"enabled": False}}, headers=TENANT_HEADERS)
    assert resp.status_code == 201


async def test_delete_setting(client):
    setting = (await client.post("/api/v1/rbac/settings", json={"category": "general", "key": "site_name", "value": {"value": "VOYQI"}}, headers=TENANT_HEADERS)).json()
    resp = await client.delete(f"/api/v1/rbac/settings/{setting['id']}", headers=TENANT_HEADERS)
    assert resp.json()["deleted"] is True
    listed = (await client.get("/api/v1/rbac/settings", headers=TENANT_HEADERS)).json()
    assert not any(s["id"] == setting["id"] for s in listed)


async def test_delete_nonexistent_setting_404(client):
    import uuid
    resp = await client.delete(f"/api/v1/rbac/settings/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_list_all_settings_without_category_filter(client):
    await client.post("/api/v1/rbac/settings", json={"category": "payment_gateway", "key": "paypal", "value": {}}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/settings", json={"category": "branding", "key": "logo_url", "value": {}}, headers=TENANT_HEADERS)
    listed = (await client.get("/api/v1/rbac/settings", headers=TENANT_HEADERS)).json()
    categories = {s["category"] for s in listed}
    assert "payment_gateway" in categories and "branding" in categories


async def test_ai_content_assist_category_accepted(client):
    """Regression: the AI Assist Content Settings page writes to this
    exact category — it silently 422'd until this value was added to
    the SettingCategory enum, caught by a live integration test."""
    resp = await client.post("/api/v1/rbac/settings", json={
        "category": "ai_content_assist", "key": "config",
        "value": {"brand_voice": "Confident, precise.", "default_model": "claude-opus-4-8", "auto_generate": True, "review_required": True},
    }, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    listed = (await client.get("/api/v1/rbac/settings", params={"category": "ai_content_assist"}, headers=TENANT_HEADERS)).json()
    assert listed[0]["value"]["brand_voice"] == "Confident, precise."
