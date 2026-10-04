from tests.conftest import TENANT_HEADERS


async def test_upload_and_list_media_asset(client):
    resp = await client.post("/api/v1/rbac/media", json={"filename": "hero-bg.jpg", "url": "https://cdn.example.com/hero-bg.jpg", "alt_text": "Hero background", "category": "hero"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    listed = (await client.get("/api/v1/rbac/media", headers=TENANT_HEADERS)).json()
    assert any(a["filename"] == "hero-bg.jpg" for a in listed)


async def test_filter_media_by_category(client):
    await client.post("/api/v1/rbac/media", json={"filename": "icon1.png", "url": "https://cdn.example.com/icon1.png", "category": "icon"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/media", json={"filename": "gallery1.jpg", "url": "https://cdn.example.com/gallery1.jpg", "category": "gallery"}, headers=TENANT_HEADERS)
    icons = (await client.get("/api/v1/rbac/media", params={"category": "icon"}, headers=TENANT_HEADERS)).json()
    assert all(a["category"] == "icon" for a in icons)
    assert any(a["filename"] == "icon1.png" for a in icons)


async def test_delete_media_asset(client):
    asset = (await client.post("/api/v1/rbac/media", json={"filename": "temp.jpg", "url": "https://cdn.example.com/temp.jpg"}, headers=TENANT_HEADERS)).json()
    resp = await client.delete(f"/api/v1/rbac/media/{asset['id']}", headers=TENANT_HEADERS)
    assert resp.json()["deleted"] is True


async def test_delete_nonexistent_asset_404(client):
    import uuid
    resp = await client.delete(f"/api/v1/rbac/media/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404
