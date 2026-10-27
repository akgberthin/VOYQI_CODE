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
