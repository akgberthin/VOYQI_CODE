"""Outside research: Reddit discussions, Google News and YouTube.
Results are data for the agents to read, never instructions — the
engine flags anything instruction-like before an agent sees it."""
from xml.etree import ElementTree

from src.seo.adapters.base import ProviderError, ProviderNotConfigured, request

UA = {"User-Agent": "VOYQI-SEO-Research/1.0 (+https://voyqi.com/bot)"}


async def reddit(query: str, creds: dict, limit: int = 10) -> list[dict]:
    """Uses an app-only OAuth token when 'reddit' credentials are set
    (reliable from servers); the public search otherwise."""
    params = {"q": query, "sort": "relevance", "t": "year", "limit": limit, "type": "link"}
    if creds.get("client_id") and creds.get("client_secret"):
        token = await request("POST", "https://www.reddit.com/api/v1/access_token", what="Reddit", headers=UA,
                              auth=(creds["client_id"], creds["client_secret"]), data={"grant_type": "client_credentials"})
        resp = await request("GET", "https://oauth.reddit.com/search", what="Reddit", params=params,
                             headers={**UA, "Authorization": f"Bearer {token.json()['access_token']}"})
    else:
        resp = await request("GET", "https://www.reddit.com/search.json", what="Reddit", params=params, headers=UA)
    out = []
    for child in (resp.json().get("data") or {}).get("children", []):
        d = child.get("data") or {}
        out.append({"title": d.get("title", ""), "url": f"https://www.reddit.com{d.get('permalink', '')}",
                    "summary": (d.get("selftext") or "")[:600],
                    "signals": {"subreddit": d.get("subreddit"), "score": d.get("score"), "comments": d.get("num_comments")}})
    return out


async def google_news(query: str, config: dict, limit: int = 10) -> list[dict]:
    lang, country = config.get("language") or "en-US", config.get("country") or "US"
    resp = await request("GET", "https://news.google.com/rss/search", what="Google News", headers=UA,
                         params={"q": query, "hl": lang, "gl": country, "ceid": f"{country}:{lang.split('-')[0]}"})
    try:
        root = ElementTree.fromstring(resp.content)
    except ElementTree.ParseError as exc:
        raise ProviderError("Google News did not return a readable feed") from exc
    out = []
    for item in root.iter("item"):
        source = item.find("source")
        out.append({"title": item.findtext("title") or "", "url": item.findtext("link") or "", "summary": "",
                    "signals": {"published": item.findtext("pubDate"), "source": source.text if source is not None else None}})
        if len(out) >= limit:
            break
    return out


async def youtube(query: str, creds: dict, limit: int = 10) -> list[dict]:
    if not creds.get("api_key"):
        raise ProviderNotConfigured("the 'youtube' integration needs a YouTube Data API key")
    resp = await request("GET", "https://www.googleapis.com/youtube/v3/search", what="YouTube", params={
        "part": "snippet", "q": query, "type": "video", "maxResults": limit, "key": creds["api_key"], "order": "relevance"})
    out = []
    for item in resp.json().get("items", []):
        s = item.get("snippet") or {}
        vid = (item.get("id") or {}).get("videoId")
        if vid:
            out.append({"title": s.get("title", ""), "url": f"https://www.youtube.com/watch?v={vid}",
                        "summary": (s.get("description") or "")[:600],
                        "signals": {"channel": s.get("channelTitle"), "published": s.get("publishedAt")}})
    return out
