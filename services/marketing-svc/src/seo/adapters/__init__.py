"""One door to every outside system. The engine calls these with a site
and its integration row; the provider on the row picks the adapter and
the super admin's encrypted store supplies the credentials."""
import asyncio
from typing import Any

from src.core.config import get_settings
from src.seo.adapters import cms, crawler, dataforseo, geo, google, research, sample
from src.seo.adapters.base import ProviderError, ProviderNotConfigured, credential_provider, credentials, require_adapter
from src.seo.models import SeoIntegration, SeoSite

__all__ = ["ProviderError", "ProviderNotConfigured"]


def _check(integ: SeoIntegration) -> str:
    require_adapter(integ.kind, integ.provider)
    if integ.provider == "sample" and not get_settings().seo_sample_adapters:
        raise ProviderNotConfigured("sample adapters are switched off in this deployment (MARKETING_SEO_SAMPLE_ADAPTERS)",
                                    kind=integ.kind, provider="sample")
    return integ.provider


async def _creds(site: SeoSite, integ: SeoIntegration, required: bool = True) -> dict:
    return await credentials(site.tenant_id, credential_provider(integ), required=required)


def _profile(site: SeoSite) -> dict:
    return site.business_profile or {}


async def test(site: SeoSite, integ: SeoIntegration) -> str:
    """A cheap, read-only call proving the connection works."""
    provider = _check(integ)
    if provider == "sample":
        return "sample adapter (demo data, not a real connection)"
    kind, cfg = integ.kind, integ.config or {}
    if kind in ("cms", "website_builder"):
        creds = await _creds(site, integ)
        return await (cms.wp_test(creds) if provider == "wordpress" else cms.hook_test(creds))
    if kind == "search_console":
        return await google.gsc_test(site.domain, cfg, await _creds(site, integ))
    if kind == "analytics":
        return await google.ga4_test(cfg, await _creds(site, integ))
    if kind == "business_profile":
        return "locations are kept up to date by hand" if provider == "manual" else await google.gbp_test(await _creds(site, integ))
    if kind in ("serp_data", "keyword_data", "backlink_index"):
        return await dataforseo.test(await _creds(site, integ))
    if kind == "crawler":
        page = await crawler.fetch_page(site.domain, cfg.get("start_url") or f"https://{site.domain}/")
        return f"fetched {page['url']} ({page['words']} words)"
    if kind == "geo_engine":
        engines = await _geo_engines(site, cfg)
        if not engines:
            raise ProviderNotConfigured("no AI engine has credentials: set up openai, perplexity or anthropic in the super admin console")
        return f"engines ready: {', '.join(engines)}"
    if kind == "community_research":
        items = await research.reddit(_seed(site), await credentials(site.tenant_id, "reddit", required=False), limit=1)
        return f"Reddit search answered ({len(items)} result)"
    if kind == "news_research":
        items = await research.google_news(_seed(site), cfg, limit=1)
        return f"Google News answered ({len(items)} result)"
    if kind == "video_research":
        items = await research.youtube(_seed(site), await _creds(site, integ), limit=1)
        return f"YouTube answered ({len(items)} result)"
    if kind == "email_inbox":
        creds = await credentials(site.tenant_id, "seo_inbox")
        if not creds.get("token"):
            raise ProviderNotConfigured("set a token on the 'seo_inbox' integration in the super admin console")
        return f"forward requests to {get_settings().public_base_url}/api/v1/marketing/public/seo/inbound/{site.id}?token=…"
    raise ProviderError(f"no test for {kind}")


def _seed(site: SeoSite) -> str:
    profile = _profile(site)
    return (profile.get("products") or profile.get("topics") or [site.name])[0]


# ── CMS ───────────────────────────────────────────────────────────

async def publish(site: SeoSite, integ: SeoIntegration, item: dict) -> dict:
    provider = _check(integ)
    if provider == "sample":
        return {"url": item.get("refresh_of_url") or f"https://{site.domain}/{item['slug']}", "ref": f"sample-{item['slug'][:20]}"}
    creds = await _creds(site, integ)
    return await (cms.wp_publish(creds, item) if provider == "wordpress" else cms.hook_publish(creds, item))


async def apply_fix(site: SeoSite, integ: SeoIntegration, fix_type: str, params: dict) -> str:
    provider = _check(integ)
    if provider == "sample":
        return "applied by the sample adapter"
    creds = await _creds(site, integ)
    return await (cms.wp_fix(creds, fix_type, params) if provider == "wordpress" else cms.hook_fix(creds, fix_type, params))


# ── measurement ───────────────────────────────────────────────────

async def search_console_sync(site: SeoSite, integ: SeoIntegration) -> dict:
    if _check(integ) == "sample":
        return sample.gsc_sync(site.domain, _profile(site))
    return await google.gsc_sync(site.domain, integ.config or {}, await _creds(site, integ))


async def analytics_sync(site: SeoSite, integ: SeoIntegration) -> dict:
    if _check(integ) == "sample":
        return sample.ga4_sync(site.domain)
    return await google.ga4_sync(integ.config or {}, await _creds(site, integ))


async def ranked_keywords(site: SeoSite, integ: SeoIntegration, domain: str) -> list[dict]:
    if _check(integ) == "sample":
        return sample.ranked_keywords(domain, _profile(site), owner_is_site=domain == site.domain)
    return await dataforseo.ranked_keywords(await _creds(site, integ), integ.config or {}, domain,
                                            int((integ.config or {}).get("keyword_limit") or 200))


async def keyword_ideas(site: SeoSite, integ: SeoIntegration, seed: str) -> list[dict]:
    if _check(integ) == "sample":
        return sample.keyword_ideas(seed)
    return await dataforseo.keyword_ideas(await _creds(site, integ), integ.config or {}, seed)


async def find_competitors(site: SeoSite, integ: SeoIntegration) -> list[dict]:
    if _check(integ) == "sample":
        return sample.competitors(site.domain, _profile(site))
    return await dataforseo.competitors(await _creds(site, integ), integ.config or {}, site.domain)


async def serp(site: SeoSite, integ: SeoIntegration, query: str, location_name: str | None = None, engine: str = "google") -> dict:
    if _check(integ) == "sample":
        return sample.serp(query if engine == "google" else f"{query} ")
    return await dataforseo.serp(await _creds(site, integ), integ.config or {}, query, location_name, engine)


async def local_pack_position(site: SeoSite, integ: SeoIntegration, keyword: str, location: Any) -> tuple[int | None, bool]:
    """(position in the local pack, whether it is in the 3-pack)."""
    city = (location.address or {}).get("city", "")
    if _check(integ) == "sample":
        pos = sample.local_rank(keyword, location.name)
        return pos, bool(pos and pos <= 3)
    region = (location.address or {}).get("region", "")
    result = await dataforseo.serp(await _creds(site, integ), integ.config or {}, keyword,
                                   location_name=",".join(p for p in (city, region, (integ.config or {}).get("country_name", "United States")) if p))
    name = location.name.lower()
    for entry in result["local_pack"]:
        if name in entry["title"].lower() or site.domain in (entry.get("domain") or ""):
            return entry["position"], (entry["position"] or 99) <= 3
    return None, False


async def backlink_summary(site: SeoSite, integ: SeoIntegration, domain: str | None = None) -> dict:
    if _check(integ) == "sample":
        return {**sample.backlink_summary(domain or site.domain), "source": "sample backlink-index import"}
    return {**await dataforseo.backlink_summary(await _creds(site, integ), domain or site.domain), "source": "dataforseo"}


async def backlinks(site: SeoSite, integ: SeoIntegration) -> list[dict]:
    if _check(integ) == "sample":
        return []
    return await dataforseo.backlinks(await _creds(site, integ), site.domain)


async def referring_domains(site: SeoSite, integ: SeoIntegration, domain: str) -> list[dict]:
    if _check(integ) == "sample":
        return sample.referring_domains(domain)
    return await dataforseo.referring_domains(await _creds(site, integ), domain)


# ── crawling ──────────────────────────────────────────────────────

async def crawl(site: SeoSite, integ: SeoIntegration) -> dict:
    _check(integ)
    psi_key = (await credentials(site.tenant_id, "google_seo", required=False)).get("pagespeed_api_key")
    return await crawler.crawl(site.domain, integ.config or {}, psi_key)


async def fetch_page(site: SeoSite, url: str) -> dict:
    return await crawler.fetch_page(site.domain, url)


# ── AI answers and research ───────────────────────────────────────

async def _geo_engines(site: SeoSite, cfg: dict) -> dict[str, dict]:
    wanted = cfg.get("engines") or list(geo.ENGINES)
    out = {}
    for engine in wanted:
        if engine not in geo.ENGINES:
            continue
        creds = await credentials(site.tenant_id, geo.ENGINES[engine][0], required=False)
        if creds.get("api_key"):
            out[engine] = creds
    return out


async def ask_engines(site: SeoSite, integ: SeoIntegration, prompt: str) -> list[dict]:
    """The prompt's answer from every engine that has credentials. An
    engine that fails is reported, the others still count."""
    _check(integ)
    engines = await _geo_engines(site, integ.config or {})
    if not engines:
        raise ProviderNotConfigured("no AI engine has credentials: set up openai, perplexity or anthropic in the super admin console",
                                    kind="geo_engine")

    async def one(engine: str, creds: dict) -> dict:
        try:
            return await geo.ENGINES[engine][1](prompt, creds, integ.config or {})
        except ProviderError as exc:
            return {"engine": engine, "error": exc.detail}

    return list(await asyncio.gather(*(one(e, c) for e, c in engines.items())))


async def research_items(site: SeoSite, integ: SeoIntegration, source: str, query: str) -> list[dict]:
    _check(integ)
    if source == "reddit":
        return await research.reddit(query, await credentials(site.tenant_id, "reddit", required=False))
    if source == "news":
        return await research.google_news(query, integ.config or {})
    if source == "youtube":
        return await research.youtube(query, await _creds(site, integ))
    raise ProviderError(f"unknown research source {source}")


# ── Business Profile ──────────────────────────────────────────────

async def gbp_pull(site: SeoSite, integ: SeoIntegration, location_id: str) -> dict | None:
    if _check(integ) == "manual":
        return None
    return await google.gbp_pull(await _creds(site, integ), location_id)


async def gbp_push(site: SeoSite, integ: SeoIntegration, location_id: str, change: dict) -> str | None:
    """Applies a change to the live profile; None when profiles are kept
    by hand (the change then waits for a person to copy it over)."""
    if _check(integ) == "manual":
        return None
    creds = await _creds(site, integ)
    return await google.gbp_push(creds, (integ.config or {}).get("account_id") or creds.get("account_id", ""), location_id, change)
