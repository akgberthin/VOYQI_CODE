from tests.conftest import TENANT_HEADERS


async def _create_page(client):
    return (await client.post("/api/v1/rbac/page-content", json={"page_key": "landing", "title": "Landing Page"}, headers=TENANT_HEADERS)).json()


async def test_create_and_list_sections_ordered(client):
    page = await _create_page(client)
    await client.post("/api/v1/rbac/sections", json={"page_id": page["id"], "section_key": "hero", "section_type": "hero", "title": "Hero"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/sections", json={"page_id": page["id"], "section_key": "features", "section_type": "features", "title": "Features"}, headers=TENANT_HEADERS)
    sections = (await client.get("/api/v1/rbac/sections", params={"page_id": page["id"]}, headers=TENANT_HEADERS)).json()
    assert len(sections) == 2
    assert sections[0]["order_index"] == 0
    assert sections[1]["order_index"] == 1


async def test_section_visibility_independent_of_subsection_visibility(client):
    page = await _create_page(client)
    section = (await client.post("/api/v1/rbac/sections", json={"page_id": page["id"], "section_key": "hero", "title": "Hero"}, headers=TENANT_HEADERS)).json()
    sub = (await client.post("/api/v1/rbac/sections/subsections", json={"section_id": section["id"], "subsection_key": "headline", "content": "Welcome"}, headers=TENANT_HEADERS)).json()

    # Hide the subsection only — section itself must stay visible.
    await client.patch(f"/api/v1/rbac/sections/subsections/{sub['id']}", json={"visible": False}, headers=TENANT_HEADERS)
    section_after = (await client.get("/api/v1/rbac/sections", params={"page_id": page["id"]}, headers=TENANT_HEADERS)).json()[0]
    assert section_after["visible"] is True

    subs = (await client.get("/api/v1/rbac/sections/subsections", params={"section_id": section["id"]}, headers=TENANT_HEADERS)).json()
    assert subs[0]["visible"] is False


async def test_hiding_section_does_not_touch_subsection_visibility(client):
    page = await _create_page(client)
    section = (await client.post("/api/v1/rbac/sections", json={"page_id": page["id"], "section_key": "hero", "title": "Hero"}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/rbac/sections/subsections", json={"section_id": section["id"], "subsection_key": "headline", "content": "Welcome"}, headers=TENANT_HEADERS)

    await client.patch(f"/api/v1/rbac/sections/{section['id']}", json={"visible": False}, headers=TENANT_HEADERS)
    subs = (await client.get("/api/v1/rbac/sections/subsections", params={"section_id": section["id"]}, headers=TENANT_HEADERS)).json()
    assert subs[0]["visible"] is True  # untouched by the parent's visibility change


async def test_parallax_settings_update(client):
    page = await _create_page(client)
    section = (await client.post("/api/v1/rbac/sections", json={"page_id": page["id"], "section_key": "hero", "title": "Hero"}, headers=TENANT_HEADERS)).json()
    updated = (await client.patch(f"/api/v1/rbac/sections/{section['id']}", json={
        "parallax_enabled": True, "parallax_speed": 0.8, "parallax_image_url": "https://cdn.example.com/hero-bg.jpg",
    }, headers=TENANT_HEADERS)).json()
    assert updated["parallax_enabled"] is True
    assert updated["parallax_speed"] == 0.8
    assert updated["parallax_image_url"] == "https://cdn.example.com/hero-bg.jpg"


async def test_delete_section_cascades_to_subsections(client):
    page = await _create_page(client)
    section = (await client.post("/api/v1/rbac/sections", json={"page_id": page["id"], "section_key": "hero", "title": "Hero"}, headers=TENANT_HEADERS)).json()
    await client.post("/api/v1/rbac/sections/subsections", json={"section_id": section["id"], "subsection_key": "headline", "content": "Welcome"}, headers=TENANT_HEADERS)

    resp = await client.delete(f"/api/v1/rbac/sections/{section['id']}", headers=TENANT_HEADERS)
    assert resp.json()["deleted"] is True
    subs = (await client.get("/api/v1/rbac/sections/subsections", params={"section_id": section["id"]}, headers=TENANT_HEADERS)).json()
    assert subs == []


async def test_update_nonexistent_section_404(client):
    import uuid
    resp = await client.patch(f"/api/v1/rbac/sections/{uuid.uuid4()}", json={"visible": False}, headers=TENANT_HEADERS)
    assert resp.status_code == 404
