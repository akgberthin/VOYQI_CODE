from tests.conftest import TENANT_HEADERS


async def test_create_page_defaults_to_draft(client):
    resp = await client.post("/api/v1/rbac/page-content", json={"page_key": "terms-of-service", "title": "Terms of Service", "body": "..."}, headers=TENANT_HEADERS)
    assert resp.status_code == 201
    assert resp.json()["status"] == "draft"


async def test_publish_and_unpublish_page(client):
    page = (await client.post("/api/v1/rbac/page-content", json={"page_key": "privacy-policy", "title": "Privacy Policy", "body": "..."}, headers=TENANT_HEADERS)).json()
    published = (await client.post(f"/api/v1/rbac/page-content/{page['id']}/publish", headers=TENANT_HEADERS)).json()
    assert published["status"] == "published"
    unpublished = (await client.post(f"/api/v1/rbac/page-content/{page['id']}/unpublish", headers=TENANT_HEADERS)).json()
    assert unpublished["status"] == "draft"


async def test_upsert_updates_existing_page_rather_than_duplicating(client):
    await client.post("/api/v1/rbac/page-content", json={"page_key": "landing", "title": "Welcome", "body": "v1"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/page-content", json={"page_key": "landing", "title": "Welcome", "body": "v2"}, headers=TENANT_HEADERS)
    listed = (await client.get("/api/v1/rbac/page-content", headers=TENANT_HEADERS)).json()
    matches = [p for p in listed if p["page_key"] == "landing"]
    assert len(matches) == 1
    assert matches[0]["body"] == "v2"


async def test_publish_nonexistent_page_404(client):
    import uuid
    resp = await client.post(f"/api/v1/rbac/page-content/{uuid.uuid4()}/publish", headers=TENANT_HEADERS)
    assert resp.status_code == 404


async def test_page_defaults_slug_and_seo_title_from_page_key_and_title(client):
    page = (await client.post("/api/v1/rbac/page-content", json={"page_key": "about-us", "title": "About Us"}, headers=TENANT_HEADERS)).json()
    assert page["slug"] == "about-us"
    assert page["seo_title"] == "About Us"


async def test_page_accepts_explicit_seo_fields(client):
    page = (await client.post("/api/v1/rbac/page-content", json={
        "page_key": "pricing", "title": "Pricing", "slug": "our-pricing",
        "seo_title": "VOYQI Pricing Plans", "seo_description": "Compare membership tiers.", "template": "landing",
    }, headers=TENANT_HEADERS)).json()
    assert page["slug"] == "our-pricing"
    assert page["seo_title"] == "VOYQI Pricing Plans"
    assert page["seo_description"] == "Compare membership tiers."
    assert page["template"] == "landing"


async def test_delete_page(client):
    page = (await client.post("/api/v1/rbac/page-content", json={"page_key": "temp-page", "title": "Temp"}, headers=TENANT_HEADERS)).json()
    resp = await client.delete(f"/api/v1/rbac/page-content/{page['id']}", headers=TENANT_HEADERS)
    assert resp.json()["deleted"] is True
