from tests.conftest import TENANT_HEADERS


async def test_create_theme_seeds_default_palette(client):
    theme = (await client.post("/api/v1/rbac/themes", json={"name": "Default"}, headers=TENANT_HEADERS)).json()
    colors = (await client.get(f"/api/v1/rbac/themes/{theme['id']}/colors", headers=TENANT_HEADERS)).json()
    assert len(colors) == 12  # every ThemeColorKey seeded
    keys = {c["color_key"] for c in colors}
    assert "primary" in keys and "background" in keys


async def test_create_theme_without_seeding(client):
    theme = (await client.post("/api/v1/rbac/themes", json={"name": "Blank", "seed_defaults": False}, headers=TENANT_HEADERS)).json()
    colors = (await client.get(f"/api/v1/rbac/themes/{theme['id']}/colors", headers=TENANT_HEADERS)).json()
    assert colors == []


async def test_set_theme_color_updates_existing_rather_than_duplicating(client):
    theme = (await client.post("/api/v1/rbac/themes", json={"name": "Custom"}, headers=TENANT_HEADERS)).json()
    await client.post(f"/api/v1/rbac/themes/{theme['id']}/colors", json={"color_key": "primary", "hex_value": "#ff0000"}, headers=TENANT_HEADERS)
    colors = (await client.get(f"/api/v1/rbac/themes/{theme['id']}/colors", headers=TENANT_HEADERS)).json()
    matches = [c for c in colors if c["color_key"] == "primary"]
    assert len(matches) == 1
    assert matches[0]["hex_value"] == "#ff0000"


async def test_activate_theme_is_exclusive(client):
    """Exactly one theme is active at a time — activating a second
    theme must deactivate the first, structurally, not by convention."""
    theme_a = (await client.post("/api/v1/rbac/themes", json={"name": "Theme A"}, headers=TENANT_HEADERS)).json()
    theme_b = (await client.post("/api/v1/rbac/themes", json={"name": "Theme B"}, headers=TENANT_HEADERS)).json()

    await client.post(f"/api/v1/rbac/themes/{theme_a['id']}/activate", headers=TENANT_HEADERS)
    active = (await client.get("/api/v1/rbac/themes/active", headers=TENANT_HEADERS)).json()
    assert active["id"] == theme_a["id"]

    await client.post(f"/api/v1/rbac/themes/{theme_b['id']}/activate", headers=TENANT_HEADERS)
    active2 = (await client.get("/api/v1/rbac/themes/active", headers=TENANT_HEADERS)).json()
    assert active2["id"] == theme_b["id"]

    all_themes = (await client.get("/api/v1/rbac/themes", headers=TENANT_HEADERS)).json()
    active_count = sum(1 for t in all_themes if t["is_active"])
    assert active_count == 1


async def test_no_active_theme_returns_404(client):
    resp = await client.get("/api/v1/rbac/themes/active", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_delete_theme_cascades_colors(client):
    theme = (await client.post("/api/v1/rbac/themes", json={"name": "Temp"}, headers=TENANT_HEADERS)).json()
    resp = await client.delete(f"/api/v1/rbac/themes/{theme['id']}", headers=TENANT_HEADERS)
    assert resp.json()["deleted"] is True
    colors = (await client.get(f"/api/v1/rbac/themes/{theme['id']}/colors", headers=TENANT_HEADERS)).json()
    assert colors == []
