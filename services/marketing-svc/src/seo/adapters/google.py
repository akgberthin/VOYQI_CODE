"""Google: Search Console and GA4 through a service account (added as a
user on the property), Business Profile through an OAuth refresh token
(the Business Profile APIs do not accept service accounts)."""
import json
import time
from datetime import date, timedelta
from urllib.parse import quote

from src.seo.adapters.base import ProviderError, ProviderNotConfigured, request

TOKEN_URI = "https://oauth2.googleapis.com/token"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

_tokens: dict[tuple[str, str], tuple[float, str]] = {}


async def service_account_token(service_account_json: str, scope: str) -> str:
    import jwt  # PyJWT with the cryptography backend

    try:
        account = json.loads(service_account_json)
        email, key = account["client_email"], account["private_key"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderNotConfigured(f"the Google service account JSON is not valid ({exc})") from exc
    cached = _tokens.get((email, scope))
    if cached and cached[0] > time.time() + 60:
        return cached[1]
    now = int(time.time())
    assertion = jwt.encode({"iss": email, "scope": scope, "aud": account.get("token_uri", TOKEN_URI), "iat": now, "exp": now + 3600},
                           key, algorithm="RS256")
    resp = await request("POST", account.get("token_uri", TOKEN_URI), what="Google sign-in",
                         data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    token = resp.json()["access_token"]
    _tokens[(email, scope)] = (now + 3500, token)
    return token


def _sa(creds: dict) -> str:
    sa = creds.get("service_account_json")
    if not sa:
        raise ProviderNotConfigured("the Google service account JSON is missing from the 'google_seo' integration")
    return sa


# ── Search Console ────────────────────────────────────────────────

def gsc_property(site_domain: str, config: dict) -> str:
    return config.get("property") or f"sc-domain:{site_domain}"


async def _gsc_query(token: str, prop: str, body: dict) -> list[dict]:
    url = f"https://www.googleapis.com/webmasters/v3/sites/{quote(prop, safe='')}/searchAnalytics/query"
    resp = await request("POST", url, what="Search Console", headers={"Authorization": f"Bearer {token}"}, json=body)
    return resp.json().get("rows", [])


async def gsc_test(site_domain: str, config: dict, creds: dict) -> str:
    token = await service_account_token(_sa(creds), GSC_SCOPE)
    prop = gsc_property(site_domain, config)
    end = date.today() - timedelta(days=2)
    await _gsc_query(token, prop, {"startDate": str(end - timedelta(days=7)), "endDate": str(end), "dimensions": ["date"], "rowLimit": 1})
    return f"Search Console property {prop} is readable"


async def gsc_sync(site_domain: str, config: dict, creds: dict) -> dict:
    """56 days of daily totals, per-page figures for the latest 28 days
    and the 28 before, and query→page positions for the latest 28."""
    token = await service_account_token(_sa(creds), GSC_SCOPE)
    prop = gsc_property(site_domain, config)
    end = date.today() - timedelta(days=2)  # Search Console data lags about two days
    cur_start, prev_end = end - timedelta(days=27), end - timedelta(days=28)
    prev_start = prev_end - timedelta(days=27)
    daily = await _gsc_query(token, prop, {"startDate": str(prev_start), "endDate": str(end), "dimensions": ["date"], "rowLimit": 100})
    pages_cur = await _gsc_query(token, prop, {"startDate": str(cur_start), "endDate": str(end), "dimensions": ["page"], "rowLimit": 1000})
    pages_prev = await _gsc_query(token, prop, {"startDate": str(prev_start), "endDate": str(prev_end), "dimensions": ["page"], "rowLimit": 1000})
    queries = await _gsc_query(token, prop, {"startDate": str(cur_start), "endDate": str(end), "dimensions": ["query", "page"], "rowLimit": 5000})

    def page_rows(rows: list[dict]) -> list[dict]:
        return [{"url": r["keys"][0], "clicks": int(r.get("clicks", 0)), "impressions": int(r.get("impressions", 0)),
                 "position": round(r.get("position", 0), 1)} for r in rows]

    return {
        "as_of": str(end),
        "daily": [{"day": r["keys"][0], "clicks": int(r.get("clicks", 0)), "impressions": int(r.get("impressions", 0)),
                   "position": round(r.get("position", 0), 2)} for r in daily],
        "pages_current": page_rows(pages_cur), "pages_previous": page_rows(pages_prev),
        "queries": [{"query": r["keys"][0], "url": r["keys"][1], "clicks": int(r.get("clicks", 0)),
                     "impressions": int(r.get("impressions", 0)), "position": round(r.get("position", 0), 1)} for r in queries],
    }


# ── GA4 ───────────────────────────────────────────────────────────

def _ga_property(config: dict) -> str:
    prop = str(config.get("property_id") or "").removeprefix("properties/")
    if not prop:
        raise ProviderNotConfigured("set the GA4 property_id in this integration's config")
    return prop


async def ga4_test(config: dict, creds: dict) -> str:
    token = await service_account_token(_sa(creds), GA_SCOPE)
    prop = _ga_property(config)
    await request("POST", f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport", what="Google Analytics",
                  headers={"Authorization": f"Bearer {token}"},
                  json={"dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}], "metrics": [{"name": "sessions"}], "limit": 1})
    return f"GA4 property {prop} is readable"


async def ga4_sync(config: dict, creds: dict) -> dict:
    token = await service_account_token(_sa(creds), GA_SCOPE)
    prop = _ga_property(config)
    resp = await request("POST", f"https://analyticsdata.googleapis.com/v1beta/properties/{prop}:runReport", what="Google Analytics",
                         headers={"Authorization": f"Bearer {token}"}, json={
                             "dateRanges": [{"startDate": "56daysAgo", "endDate": "yesterday"}],
                             "dimensions": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}],
                             "metrics": [{"name": "sessions"}, {"name": "keyEvents"}], "limit": 10000})
    days: dict[str, dict] = {}
    for r in resp.json().get("rows", []):
        raw_day, channel = r["dimensionValues"][0]["value"], r["dimensionValues"][1]["value"]
        sessions, key_events = (int(float(m["value"] or 0)) for m in r["metricValues"])
        day = f"{raw_day[:4]}-{raw_day[4:6]}-{raw_day[6:]}"
        d = days.setdefault(day, {"day": day, "sessions": 0, "organic_sessions": 0, "conversions": 0})
        d["sessions"] += sessions
        d["conversions"] += key_events
        if channel == "Organic Search":
            d["organic_sessions"] += sessions
    return {"daily": sorted(days.values(), key=lambda d: d["day"])}


# ── Business Profile ──────────────────────────────────────────────

async def gbp_token(creds: dict) -> str:
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not creds.get(k)]
    if missing:
        raise ProviderNotConfigured(f"the Business Profile integration is missing {', '.join(missing)}")
    resp = await request("POST", TOKEN_URI, what="Google sign-in", data={
        "grant_type": "refresh_token", "client_id": creds["client_id"], "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"]})
    return resp.json()["access_token"]


async def gbp_test(creds: dict) -> str:
    token = await gbp_token(creds)
    resp = await request("GET", "https://mybusinessaccountmanagement.googleapis.com/v1/accounts", what="Business Profile",
                         headers={"Authorization": f"Bearer {token}"})
    return f"{len(resp.json().get('accounts', []))} Business Profile account(s) reachable"


async def gbp_pull(creds: dict, location_id: str) -> dict:
    token = await gbp_token(creds)
    resp = await request("GET", f"https://mybusinessbusinessinformation.googleapis.com/v1/locations/{location_id}", what="Business Profile",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"readMask": "title,phoneNumbers,categories,storefrontAddress,websiteUri,regularHours,specialHours,profile"})
    data = resp.json()
    return {
        "name": data.get("title"), "phone": (data.get("phoneNumbers") or {}).get("primaryPhone"),
        "website": data.get("websiteUri"), "description": (data.get("profile") or {}).get("description"),
        "categories": [c.get("displayName") for c in [(data.get("categories") or {}).get("primaryCategory"),
                                                      *((data.get("categories") or {}).get("additionalCategories") or [])] if c],
        "special_hours": bool(data.get("specialHours")),
        "hours_days": len((data.get("regularHours") or {}).get("periods") or []),
    }


async def gbp_push(creds: dict, account_id: str, location_id: str, change: dict) -> str:
    """Applies one agent-made change: description, or an update post."""
    token = await gbp_token(creds)
    headers = {"Authorization": f"Bearer {token}"}
    if change["item"] == "description":
        await request("PATCH", f"https://mybusinessbusinessinformation.googleapis.com/v1/locations/{location_id}", what="Business Profile",
                      headers=headers, params={"updateMask": "profile.description"}, json={"profile": {"description": change["value"]}})
        return "description updated"
    if change["item"] == "posts":
        if not account_id:
            raise ProviderNotConfigured("set account_id on the Business Profile integration to publish posts")
        await request("POST", f"https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/localPosts",
                      what="Business Profile", headers=headers,
                      json={"languageCode": "en", "summary": change["value"][:1500], "topicType": "STANDARD"})
        return "update post published"
    raise ProviderError(f"Business Profile changes of type '{change['item']}' are applied by a person in the profile")
