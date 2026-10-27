"""DataForSEO (https://dataforseo.com): one account covers keyword data,
search results, local packs and the backlink index. Live endpoints,
pay-per-call; credentials are the API login and password."""
from typing import Any

from src.seo.adapters.base import ProviderError, ProviderNotConfigured, request

BASE = "https://api.dataforseo.com/v3"
TASK_OK = 20000


def _auth(creds: dict) -> tuple[str, str]:
    if not creds.get("login") or not creds.get("password"):
        raise ProviderNotConfigured("the 'dataforseo' integration needs the API login and password")
    return creds["login"], creds["password"]


def _geo(config: dict) -> dict:
    return {"location_code": int(config.get("location_code") or 2840), "language_code": config.get("language_code") or "en"}


async def _post(creds: dict, path: str, task: dict) -> list[dict]:
    resp = await request("POST", f"{BASE}{path}", what="DataForSEO", auth=_auth(creds), json=[task], timeout=60.0)
    body = resp.json()
    tasks = body.get("tasks") or []
    if body.get("status_code") != TASK_OK or not tasks:
        raise ProviderError(f"DataForSEO: {body.get('status_message', 'no result')}")
    if tasks[0].get("status_code") != TASK_OK:
        raise ProviderError(f"DataForSEO: {tasks[0].get('status_message', 'task failed')}")
    return tasks[0].get("result") or []


def _items(result: list[dict]) -> list[dict]:
    return (result[0].get("items") if result else None) or []


async def test(creds: dict) -> str:
    resp = await request("GET", f"{BASE}/appendix/user_data", what="DataForSEO", auth=_auth(creds))
    body = resp.json()
    if body.get("status_code") != TASK_OK:
        raise ProviderError(f"DataForSEO: {body.get('status_message')}")
    result = (body.get("tasks") or [{}])[0].get("result") or [{}]
    balance = (result[0].get("money") or {}).get("balance")
    return f"DataForSEO account reachable (balance ${balance})" if balance is not None else "DataForSEO account reachable"


def _growth(info: dict) -> tuple[str, float | None]:
    monthly = [m.get("search_volume") or 0 for m in (info.get("monthly_searches") or [])]
    if len(monthly) < 6:
        return "flat", None
    recent, earlier = sum(monthly[:3]), sum(monthly[3:6])
    if not earlier:
        return "flat", None
    growth = round((recent - earlier) / earlier * 100, 1)
    return ("rising" if growth >= 10 else "falling" if growth <= -10 else "flat"), growth


def _keyword_row(data: dict, position: int | None = None, url: str | None = None) -> dict:
    info = data.get("keyword_info") or {}
    props = data.get("keyword_properties") or {}
    trend, growth = _growth(info)
    cpc = info.get("cpc")
    return {"term": (data.get("keyword") or "").lower(), "volume": info.get("search_volume"),
            "difficulty": props.get("keyword_difficulty"), "cpc_cents": int(cpc * 100) if cpc else None,
            "position": position, "url": url, "trend": trend, "growth_pct": growth}


async def ranked_keywords(creds: dict, config: dict, domain: str, limit: int = 200) -> list[dict]:
    """Keywords a domain ranks for, with where it ranks."""
    items = _items(await _post(creds, "/dataforseo_labs/google/ranked_keywords/live", {"target": domain, "limit": limit, **_geo(config)}))
    out = []
    for item in items:
        serp = (item.get("ranked_serp_element") or {}).get("serp_item") or {}
        out.append(_keyword_row(item.get("keyword_data") or {}, serp.get("rank_absolute"), serp.get("url")))
    return [k for k in out if k["term"]]


async def keyword_ideas(creds: dict, config: dict, seed: str, limit: int = 50) -> list[dict]:
    items = _items(await _post(creds, "/dataforseo_labs/google/keyword_suggestions/live", {"keyword": seed, "limit": limit, **_geo(config)}))
    return [r for r in (_keyword_row(i) for i in items) if r["term"]]


async def competitors(creds: dict, config: dict, domain: str, limit: int = 8) -> list[dict]:
    items = _items(await _post(creds, "/dataforseo_labs/google/competitors_domain/live", {"target": domain, "limit": limit, **_geo(config)}))
    return [{"domain": i["domain"], "shared_keywords": i.get("intersections"), "avg_position": i.get("avg_position")}
            for i in items if i.get("domain") and i["domain"] != domain]


async def serp(creds: dict, config: dict, query: str, location_name: str | None = None, engine: str = "google") -> dict:
    """Top organic results and which result features appear (engine:
    google or bing)."""
    task: dict[str, Any] = {"keyword": query, "depth": 10, "language_code": _geo(config)["language_code"]}
    if location_name:
        task["location_name"] = location_name
    else:
        task["location_code"] = _geo(config)["location_code"]
    items = _items(await _post(creds, f"/serp/{engine}/organic/live/advanced", task))
    features = sorted({i.get("type") for i in items if i.get("type") and i.get("type") != "organic"})
    organic = [{"title": i.get("title") or "", "url": i.get("url") or "", "position": i.get("rank_absolute"),
                "description": i.get("description") or ""} for i in items if i.get("type") == "organic"]
    local_pack = [{"title": i.get("title") or "", "domain": i.get("domain") or "", "position": i.get("rank_group")}
                  for i in items if i.get("type") == "local_pack"]
    return {"organic": organic, "features": features, "local_pack": local_pack}


async def backlink_summary(creds: dict, domain: str) -> dict:
    result = await _post(creds, "/backlinks/summary/live", {"target": domain, "include_subdomains": True})
    data = result[0] if result else {}
    rank = data.get("rank")
    return {"authority_score": round(rank / 10, 1) if rank is not None else None,
            "referring_domains": data.get("referring_domains"), "backlinks": data.get("backlinks")}


async def backlinks(creds: dict, domain: str, limit: int = 200) -> list[dict]:
    items = _items(await _post(creds, "/backlinks/backlinks/live", {"target": domain, "limit": limit, "mode": "one_per_domain"}))
    return [{"source_url": i.get("url_from"), "source_domain": i.get("domain_from"), "target_url": i.get("url_to"),
             "anchor": i.get("anchor") or "", "rel": "follow" if i.get("dofollow") else "nofollow",
             "authority": round((i.get("domain_from_rank") or 0) / 10, 1)} for i in items if i.get("url_from")]


async def referring_domains(creds: dict, domain: str, limit: int = 100) -> list[dict]:
    items = _items(await _post(creds, "/backlinks/referring_domains/live", {"target": domain, "limit": limit,
                                                                          "order_by": ["rank,desc"]}))
    return [{"domain": i.get("domain"), "authority": round((i.get("rank") or 0) / 10, 1),
             "spam_score": i.get("backlinks_spam_score") or 0} for i in items if i.get("domain")]
