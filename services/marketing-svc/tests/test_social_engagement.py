"""Engagement read back from each platform with the tenant's credentials."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.core import integrations
from src.models import SocialAccount, SocialPlatform, SocialPost, SocialPostStatus
from src.services import social_adapters, social_engine
from tests.conftest import TENANT_HEADERS

TENANT = TENANT_HEADERS["X-Tenant-Id"]
CREDS = {"meta": {"page_id": "p1", "page_access_token": "EAA"}, "linkedin": {"organization_urn": "urn:li:organization:1", "access_token": "li"},
         "x": {"access_token": "xt"}}


@pytest.fixture
def platforms(monkeypatch):
    seen = []
    real = httpx.AsyncClient

    def handler(req: httpx.Request):
        seen.append(req)
        url = str(req.url)
        if "/insights" in url:
            return httpx.Response(200, json={"data": [{"values": [{"value": 1200}]}]})
        if "graph.facebook.com" in url and "like_count" in url:
            return httpx.Response(200, json={"like_count": 40, "comments_count": 3})
        if "graph.facebook.com" in url:
            return httpx.Response(200, json={"reactions": {"summary": {"total_count": 12}}, "comments": {"summary": {"total_count": 4}}, "shares": {"count": 2}})
        if "linkedin.com" in url:
            return httpx.Response(200, json={"likesSummary": {"totalLikes": 9}, "commentsSummary": {"aggregatedTotalComments": 1}})
        if "api.x.com" in url:
            return httpx.Response(200, json={"data": {"public_metrics": {"like_count": 5, "reply_count": 1, "retweet_count": 2, "quote_count": 1, "impression_count": 800}}})
        return httpx.Response(404)

    async def creds(tenant_id, provider):
        return CREDS.get(provider)

    monkeypatch.setattr(social_adapters.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(integrations, "get", creds)
    return seen


async def _post(db, platform, mode="real", post_id="123", published_ago=timedelta(days=1)):
    acct = SocialAccount(tenant_id=TENANT, platform=SocialPlatform(platform), handle="@v", connected=True, access_token_ref="")
    db.add(acct)
    await db.flush()
    post = SocialPost(tenant_id=TENANT, social_account_id=acct.id, content_draft_id=acct.id, scheduled_at=datetime.now(timezone.utc),
                      status=SocialPostStatus.PUBLISHED, published_at=datetime.now(timezone.utc) - published_ago, platform_post_id=post_id, delivery_mode=mode)
    db.add(post)
    await db.commit()
    return post


@pytest.mark.parametrize("platform,expected", [
    ("facebook", (12, 4, 2, 1200)), ("instagram", (40, 3, None, 1200)), ("linkedin", (9, 1, None, None)), ("twitter", (5, 1, 3, 800)),
])
async def test_each_platform_reports_its_engagement(db_session, platforms, platform, expected):
    post = await _post(db_session, platform, post_id="urn:li:share:9" if platform == "linkedin" else "123")
    p = await social_engine.refresh_metrics(db_session, TENANT, post.id)
    assert (p.likes, p.comments, p.shares, p.impressions) == expected and p.metrics_error == ""
    auth = platforms[0].headers.get("Authorization", "") + str(platforms[0].url)
    assert any(t in auth for t in ("EAA", "Bearer li", "Bearer xt"))


async def test_console_posts_and_missing_credentials(client, db_session, platforms, monkeypatch):
    console = await _post(db_session, "facebook", mode="console")
    r = await client.post(f"/api/v1/marketing/social/posts/{console.id}/refresh-metrics", headers=TENANT_HEADERS)
    assert r.status_code == 409

    async def none(tenant_id, provider):
        return None

    monkeypatch.setattr(integrations, "get", none)
    live = await _post(db_session, "twitter")
    r = (await client.post(f"/api/v1/marketing/social/posts/{live.id}/refresh-metrics", headers=TENANT_HEADERS)).json()
    assert "super admin" in r["metrics_error"] and r["likes"] is None


async def test_scheduled_refresh_covers_recent_live_posts_only(db_engine, db_session, platforms):
    await _post(db_session, "twitter")
    await _post(db_session, "twitter", published_ago=timedelta(days=45))
    await _post(db_session, "twitter", mode="console")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    assert await social_engine.refresh_due_metrics(factory) == 1
    assert await social_engine.refresh_due_metrics(factory) == 0  # not again within 6 hours
