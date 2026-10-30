"""Publishing to the social platforms through their own APIs, with the
tenant's credentials from Integrations. Each adapter returns the
platform's id for the new post or raises PublishError with the
platform's reason."""
import httpx

GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 30.0

# Which integration each account platform publishes through.
INTEGRATION = {"facebook": "meta", "instagram": "meta", "linkedin": "linkedin", "twitter": "x"}


class PublishError(Exception):
    pass


def _reason(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"HTTP {resp.status_code}: {err.get('message') or err}"
    detail = body.get("detail") or body.get("message") or body.get("title") or err or body
    return f"HTTP {resp.status_code}: {detail}"


async def _post(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await client.post(url, **kwargs)


async def facebook(config: dict, text: str, image_url: str | None) -> str:
    token = config["page_access_token"]
    if image_url:
        resp = await _post(f"{GRAPH}/{config['page_id']}/photos", data={"url": image_url, "caption": text, "access_token": token})
    else:
        resp = await _post(f"{GRAPH}/{config['page_id']}/feed", data={"message": text, "access_token": token})
    if resp.status_code != 200:
        raise PublishError(_reason(resp))
    body = resp.json()
    return body.get("post_id") or body["id"]


async def instagram(config: dict, text: str, image_url: str | None) -> str:
    """Instagram's API publishes media only: create a container from the
    image URL (Instagram downloads it), then publish the container."""
    account = config.get("instagram_account_id")
    if not account:
        raise PublishError("Set the Instagram business account ID under Integrations → Meta")
    if not image_url:
        raise PublishError("Instagram posts need an image — attach one to the post")
    token = config["page_access_token"]
    created = await _post(f"{GRAPH}/{account}/media", data={"image_url": image_url, "caption": text, "access_token": token})
    if created.status_code != 200:
        raise PublishError(_reason(created))
    published = await _post(f"{GRAPH}/{account}/media_publish", data={"creation_id": created.json()["id"], "access_token": token})
    if published.status_code != 200:
        raise PublishError(_reason(published))
    return published.json()["id"]


async def linkedin(config: dict, text: str, image_url: str | None) -> str:
    """Text post as the organization (images need LinkedIn's separate
    upload flow; the image link is appended instead)."""
    commentary = f"{text}\n\n{image_url}" if image_url else text
    resp = await _post("https://api.linkedin.com/rest/posts", headers={
        "Authorization": f"Bearer {config['access_token']}", "LinkedIn-Version": "202405", "X-Restli-Protocol-Version": "2.0.0",
    }, json={
        "author": config["organization_urn"], "commentary": commentary, "visibility": "PUBLIC",
        "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
        "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
    })
    if resp.status_code != 201:
        raise PublishError(_reason(resp))
    return resp.headers.get("x-restli-id", "")


async def twitter(config: dict, text: str, image_url: str | None) -> str:
    body = f"{text} {image_url}" if image_url else text
    if len(body) > 280:
        raise PublishError(f"Post is {len(body)} characters; X allows 280")
    resp = await _post("https://api.x.com/2/tweets", headers={"Authorization": f"Bearer {config['access_token']}"}, json={"text": body})
    if resp.status_code != 201:
        raise PublishError(_reason(resp))
    return resp.json()["data"]["id"]


ADAPTERS = {"facebook": facebook, "instagram": instagram, "linkedin": linkedin, "twitter": twitter}


async def publish(platform: str, config: dict, text: str, image_url: str | None) -> str:
    adapter = ADAPTERS.get(platform)
    if adapter is None:
        raise PublishError(f"Publishing to {platform} isn't supported yet")
    try:
        return await adapter(config, text, image_url)
    except httpx.HTTPError as exc:
        raise PublishError(f"{platform} unreachable ({type(exc).__name__})") from exc
    except KeyError as exc:
        raise PublishError(f"{platform} credentials are missing {exc}") from exc



# -- Engagement -----------------------------------------------------------
# Each returns {"likes", "comments", "shares", "impressions"} (None where the
# platform doesn't report a number) or raises PublishError with its reason.

async def _get(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await client.get(url, **kwargs)


async def _graph_insight(object_id: str, metric: str, token: str) -> int | None:
    resp = await _get(f"{GRAPH}/{object_id}/insights", params={"metric": metric, "access_token": token})
    if resp.status_code != 200:
        return None  # insights need extra permissions; the counts still stand
    data = resp.json().get("data") or []
    values = (data[0].get("values") or [{}]) if data else [{}]
    v = values[0].get("value")
    return v if isinstance(v, int) else None


async def facebook_metrics(config: dict, post_id: str) -> dict:
    token = config["page_access_token"]
    resp = await _get(f"{GRAPH}/{post_id}", params={"fields": "reactions.summary(true),comments.summary(true),shares", "access_token": token})
    if resp.status_code != 200:
        raise PublishError(_reason(resp))
    b = resp.json()
    return {"likes": ((b.get("reactions") or {}).get("summary") or {}).get("total_count", 0),
            "comments": ((b.get("comments") or {}).get("summary") or {}).get("total_count", 0),
            "shares": (b.get("shares") or {}).get("count", 0), "impressions": await _graph_insight(post_id, "post_impressions", token)}


async def instagram_metrics(config: dict, media_id: str) -> dict:
    token = config["page_access_token"]
    resp = await _get(f"{GRAPH}/{media_id}", params={"fields": "like_count,comments_count", "access_token": token})
    if resp.status_code != 200:
        raise PublishError(_reason(resp))
    b = resp.json()
    return {"likes": b.get("like_count"), "comments": b.get("comments_count"), "shares": None,
            "impressions": await _graph_insight(media_id, "impressions", token)}


async def linkedin_metrics(config: dict, post_urn: str) -> dict:
    from urllib.parse import quote

    resp = await _get(f"https://api.linkedin.com/rest/socialActions/{quote(post_urn, safe='')}", headers={
        "Authorization": f"Bearer {config['access_token']}", "LinkedIn-Version": "202405", "X-Restli-Protocol-Version": "2.0.0"})
    if resp.status_code != 200:
        raise PublishError(_reason(resp))
    b = resp.json()
    return {"likes": (b.get("likesSummary") or {}).get("totalLikes"), "comments": (b.get("commentsSummary") or {}).get("aggregatedTotalComments"),
            "shares": None, "impressions": None}


async def twitter_metrics(config: dict, tweet_id: str) -> dict:
    resp = await _get(f"https://api.x.com/2/tweets/{tweet_id}", params={"tweet.fields": "public_metrics"},
                      headers={"Authorization": f"Bearer {config['access_token']}"})
    if resp.status_code != 200:
        raise PublishError(_reason(resp))
    m = (resp.json().get("data") or {}).get("public_metrics") or {}
    return {"likes": m.get("like_count"), "comments": m.get("reply_count"),
            "shares": (m.get("retweet_count") or 0) + (m.get("quote_count") or 0), "impressions": m.get("impression_count")}


METRICS = {"facebook": facebook_metrics, "instagram": instagram_metrics, "linkedin": linkedin_metrics, "twitter": twitter_metrics}


async def metrics(platform: str, config: dict, post_id: str) -> dict:
    adapter = METRICS.get(platform)
    if adapter is None:
        raise PublishError(f"Reading engagement from {platform} isn't supported")
    try:
        return await adapter(config, post_id)
    except httpx.HTTPError as exc:
        raise PublishError(f"{platform} unreachable ({type(exc).__name__})") from exc
    except KeyError as exc:
        raise PublishError(f"{platform} credentials are missing {exc}") from exc
