"""Social posts publish through each platform's API with the tenant's
integration, fail with the platform's reason, run on schedule exactly
once, and go out in console mode when no integration is set up. Images
are hosted publicly and can be generated with OpenAI."""
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core import integrations
from src.services import media_engine, scheduler, social_adapters
from tests.conftest import TENANT_HEADERS

M = "/api/v1/marketing"
H = TENANT_HEADERS
TENANT = H["X-Tenant-Id"]
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def creds(monkeypatch):
    configs = {}

    async def fake_get(tenant_id, provider):
        return configs.get(provider) if tenant_id == TENANT else None

    monkeypatch.setattr(integrations, "get", fake_get)
    return configs


@pytest.fixture
def platform(monkeypatch):
    """Records outbound calls and answers like the platforms do."""
    state = {"sent": [], "answer": None}
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request):
        state["sent"].append(request)
        if state["answer"]:
            return state["answer"](request)
        url = str(request.url)
        if url.endswith("/feed"):
            return httpx.Response(200, json={"id": "123_456"})
        if url.endswith("/photos"):
            return httpx.Response(200, json={"id": "789", "post_id": "123_789"})
        if url.endswith("/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if url.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "ig-post-1"})
        if "linkedin" in url:
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:1"})
        if "api.x.com" in url:
            return httpx.Response(201, json={"data": {"id": "1850000000000000000", "text": "x"}})
        if "openai" in url:
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
        return httpx.Response(404)

    patched = lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)  # noqa: E731
    monkeypatch.setattr(social_adapters.httpx, "AsyncClient", patched)
    monkeypatch.setattr(media_engine.httpx, "AsyncClient", patched)
    return state


async def _post(client, platform_name, text="One membership. Every class. #VOYQI", image=False, when=None):
    camp = (await client.post(f"{M}/campaigns", json={"name": "Reel", "channel": "social"}, headers=H)).json()
    draft = (await client.post(f"{M}/content", json={"campaign_id": camp["id"], "channel": "social", "body_text": text}, headers=H)).json()
    account = (await client.post(f"{M}/social/accounts", json={"platform": platform_name, "handle": "@voyqi"}, headers=H)).json()
    asset_id = None
    if image:
        asset_id = (await client.post(f"{M}/media", json={"content_type": "image/png", "data_base64": base64.b64encode(PNG).decode(), "title": "hero"}, headers=H)).json()["id"]
    at = (when or datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    post = (await client.post(f"{M}/social/posts", json={"social_account_id": account["id"], "content_draft_id": draft["id"], "scheduled_at": at, "media_asset_id": asset_id}, headers=H)).json()
    return post, account, asset_id


async def _publish(client, post_id):
    return await client.post(f"{M}/social/posts/{post_id}/publish", json={}, headers=H)


async def test_without_an_integration_posts_go_out_in_console_mode(client, creds, platform):
    post, _a, _i = await _post(client, "facebook")
    done = (await _publish(client, post["id"])).json()
    assert (done["status"], done["delivery_mode"]) == ("published", "console")
    assert done["platform_post_id"].startswith("console-") and platform["sent"] == []


async def test_facebook_text_and_photo_posts(client, creds, platform):
    creds["meta"] = {"page_id": "PAGE1", "page_access_token": "EAAG-token"}
    text_post, _a, _i = await _post(client, "facebook")
    done = (await _publish(client, text_post["id"])).json()
    assert (done["status"], done["delivery_mode"], done["platform_post_id"]) == ("published", "real", "123_456")
    req = platform["sent"][-1]
    assert str(req.url) == "https://graph.facebook.com/v21.0/PAGE1/feed"
    assert parse_qs(req.content.decode()) == {"message": ["One membership. Every class. #VOYQI"], "access_token": ["EAAG-token"]}

    photo_post, _a, asset_id = await _post(client, "facebook", image=True)
    done = (await _publish(client, photo_post["id"])).json()
    assert done["platform_post_id"] == "123_789"
    form = parse_qs(platform["sent"][-1].content.decode())
    assert form["url"] == [f"http://localhost:8000/api/v1/marketing/public/media/{asset_id}"]


async def test_instagram_creates_a_container_then_publishes_it(client, creds, platform):
    creds["meta"] = {"page_id": "PAGE1", "page_access_token": "tok", "instagram_account_id": "IG1"}
    post, _a, _i = await _post(client, "instagram", image=True)
    done = (await _publish(client, post["id"])).json()
    assert (done["status"], done["platform_post_id"]) == ("published", "ig-post-1")
    create, publish = platform["sent"]
    assert str(create.url).endswith("/IG1/media") and "image_url" in parse_qs(create.content.decode())
    assert parse_qs(publish.content.decode())["creation_id"] == ["container-1"]


async def test_instagram_without_an_image_fails_with_the_reason(client, creds, platform):
    creds["meta"] = {"page_id": "PAGE1", "page_access_token": "tok", "instagram_account_id": "IG1"}
    post, _a, _i = await _post(client, "instagram")
    done = (await _publish(client, post["id"])).json()
    assert done["status"] == "failed" and "need an image" in done["error_detail"] and platform["sent"] == []


async def test_linkedin_and_x(client, creds, platform):
    creds["linkedin"] = {"organization_urn": "urn:li:organization:42", "access_token": "li-tok"}
    creds["x"] = {"access_token": "x-tok"}
    li, _a, _i = await _post(client, "linkedin")
    assert (await _publish(client, li["id"])).json()["platform_post_id"] == "urn:li:share:1"
    body = json.loads(platform["sent"][-1].content)
    assert body["author"] == "urn:li:organization:42" and body["lifecycleState"] == "PUBLISHED"
    tw, _a, _i = await _post(client, "twitter")
    assert (await _publish(client, tw["id"])).json()["platform_post_id"] == "1850000000000000000"
    assert platform["sent"][-1].headers["Authorization"] == "Bearer x-tok"
    long, _a, _i = await _post(client, "twitter", text="x" * 300)
    assert "allows 280" in (await _publish(client, long["id"])).json()["error_detail"]


async def test_platform_errors_are_reported(client, creds, platform):
    creds["meta"] = {"page_id": "PAGE1", "page_access_token": "expired"}
    platform["answer"] = lambda r: httpx.Response(400, json={"error": {"message": "Error validating access token: Session has expired"}})
    post, _a, _i = await _post(client, "facebook")
    done = (await _publish(client, post["id"])).json()
    assert done["status"] == "failed" and "Session has expired" in done["error_detail"]


async def test_disconnected_accounts_and_double_publishing(client, creds, platform):
    creds["meta"] = {"page_id": "PAGE1", "page_access_token": "tok"}
    post, account, _i = await _post(client, "facebook")
    await client.post(f"{M}/social/accounts/{account['id']}/disconnect", headers=H)
    assert "disconnected" in (await _publish(client, post["id"])).json()["error_detail"]
    post2, _a, _i = await _post(client, "facebook")
    assert (await _publish(client, post2["id"])).status_code == 200
    assert (await _publish(client, post2["id"])).status_code == 409


async def test_scheduler_publishes_due_posts_once(client, db_engine, creds, platform):
    creds["meta"] = {"page_id": "PAGE1", "page_access_token": "tok"}
    due, _a, _i = await _post(client, "facebook", when=datetime.now(timezone.utc) - timedelta(minutes=1))
    later, _a, _i = await _post(client, "facebook")
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    assert await scheduler.run_once(factory) == 1
    assert await scheduler.run_once(factory) == 0
    posts = {p["id"]: p for p in (await client.get(f"{M}/social/posts", headers=H)).json()}
    assert posts[due["id"]]["status"] == "published" and posts[later["id"]]["status"] == "scheduled"
    assert len(platform["sent"]) == 1


async def test_uploaded_images_are_served_publicly(client):
    up = await client.post(f"{M}/media", json={"content_type": "image/png", "data_base64": base64.b64encode(PNG).decode(), "title": "hero"}, headers=H)
    assert up.status_code == 201 and up.json()["url"].endswith(f"/public/media/{up.json()['id']}")
    served = await client.get(f"{M}/public/media/{up.json()['id']}")
    assert served.content == PNG and served.headers["content-type"] == "image/png"
    assert [a["id"] for a in (await client.get(f"{M}/media", headers=H)).json()] == [up.json()["id"]]
    bad = await client.post(f"{M}/media", json={"content_type": "application/pdf", "data_base64": base64.b64encode(b"%PDF").decode()}, headers=H)
    assert bad.status_code == 422


async def test_image_generation_with_openai(client, creds, platform):
    creds["openai"] = {"api_key": "sk-test", "image_model": "gpt-image-1"}
    created = (await client.post(f"{M}/media-generation", json={"prompt": "Silver Model Y at golden hour", "media_type": "image"}, headers=H)).json()
    done = {r["id"]: r for r in (await client.get(f"{M}/media-generation", headers=H)).json()}[created["id"]]
    assert (done["status"], done["provider"]) == ("completed", "openai")
    asset_path = done["result_url"].split("http://localhost:8000")[1]
    assert (await client.get(asset_path)).content == PNG
    req = platform["sent"][0]
    assert json.loads(req.content)["prompt"] == "Silver Model Y at golden hour" and req.headers["Authorization"] == "Bearer sk-test"


async def test_without_openai_requests_wait_and_video_is_refused(client, creds, platform):
    img = (await client.post(f"{M}/media-generation", json={"prompt": "x", "media_type": "image"}, headers=H)).json()
    vid = (await client.post(f"{M}/media-generation", json={"prompt": "x", "media_type": "video"}, headers=H)).json()
    rows = {r["id"]: r for r in (await client.get(f"{M}/media-generation", headers=H)).json()}
    assert rows[img["id"]]["status"] == "pending"
    assert rows[vid["id"]]["status"] == "failed" and "video provider" in rows[vid["id"]]["error_detail"]
