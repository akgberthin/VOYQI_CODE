"""Sample adapters for demos and local runs: deterministic, plausible data
built from the site's own business profile, always labelled 'sample'.
Only available when MARKETING_SEO_SAMPLE_ADAPTERS is on — never in a
real deployment, where every number must come from a real source."""
import hashlib
import random
from datetime import date, timedelta

from src.seo import rules

MODIFIERS = ["cost", "near me", "vs lease", "for business", "reviews", "how does it work", "worth it", "best", "plans", "monthly"]


def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16))


def _seeds(profile: dict) -> list[str]:
    seeds = [*(profile.get("products") or []), *(profile.get("topics") or [])]
    return list(dict.fromkeys(s.lower() for s in seeds if s)) or ["mobility membership"]


def _terms(profile: dict) -> list[str]:
    terms = []
    for seed in _seeds(profile):
        terms.append(seed)
        for m in MODIFIERS:
            terms.append(f"{m} {seed}" if m in ("best", "how does it work") else f"{seed} {m}")
        for market in profile.get("markets") or []:
            terms.append(f"{seed} {market.lower()}")
    return list(dict.fromkeys(terms))


def competitors(domain: str, profile: dict) -> list[dict]:
    seed = _seeds(profile)[0].split()[0]
    return [{"domain": d, "shared_keywords": 40 - i * 9, "avg_position": 6 + i * 3}
            for i, d in enumerate([f"rival-{seed}.example", f"{seed}flex.example", f"go{seed}.example"])]


def ranked_keywords(domain: str, profile: dict, owner_is_site: bool = True) -> list[dict]:
    out = []
    for term in _terms(profile):
        r = _rng(domain, term)
        if not owner_is_site and r.random() < 0.35:
            continue
        if owner_is_site and r.random() < 0.3:
            continue
        volume = int(r.choice([90, 210, 480, 880, 1300, 1900, 2900, 4400, 6600]))
        out.append({"term": term, "volume": volume, "difficulty": r.randint(12, 68), "cpc_cents": r.randint(80, 900),
                    "position": r.randint(1, 35), "url": f"https://{domain}/{rules.slugify(term)}",
                    "trend": r.choice(["rising", "flat", "flat", "falling"]), "growth_pct": round(r.uniform(-20, 30), 1)})
    return out


def keyword_ideas(seed: str) -> list[dict]:
    r = _rng("ideas", seed)
    ideas = [f"how much does {seed} cost", f"is {seed} worth it", f"what is {seed}", f"{seed} vs buying",
             f"does {seed} include insurance", f"{seed} pros and cons"]
    return [{"term": t, "volume": r.choice([110, 320, 720, 1600, 2400]), "difficulty": r.randint(10, 50), "cpc_cents": None,
             "position": None, "url": None, "trend": "flat", "growth_pct": None} for t in ideas]


def serp(query: str) -> dict:
    r = _rng("serp", query)
    formats = [f"{r.randint(5, 12)} best {query} options", f"How {query} works: a guide", f"{query} pricing and plans",
               f"{query} vs leasing compared", f"What is {query}?", f"{query} reviews"]
    r.shuffle(formats)
    organic = [{"title": t.capitalize(), "url": f"https://site{i}.example/{rules.slugify(t)}", "position": i + 1, "description": ""}
               for i, t in enumerate(formats)]
    features = ["people_also_ask"] + (["local_pack"] if "near me" in query or r.random() < 0.2 else [])
    return {"organic": organic, "features": features, "local_pack": []}


def local_rank(keyword: str, location_name: str) -> int | None:
    r = _rng("local", keyword, location_name)
    return None if r.random() < 0.25 else r.randint(1, 9)


def backlink_summary(domain: str) -> dict:
    r = _rng("authority", domain, date.today().isoformat()[:7])
    return {"authority_score": r.randint(20, 45), "referring_domains": r.randint(40, 120), "backlinks": r.randint(200, 900)}


def referring_domains(domain: str) -> list[dict]:
    r = _rng("refdomains", domain)
    names = ["mobilityweekly.com", "evdaily.example", "citycommuter.example", "autotrends.example", "greenfleet.example",
             "spammy-links.example", "urbanliving.example", "financeguide.example"]
    picked = [n for n in names if r.random() < 0.55] or names[:2]
    return [{"domain": n, "authority": r.randint(15, 70), "spam_score": 75 if n.startswith("spammy") else r.randint(1, 25)} for n in picked]


def gsc_sync(domain: str, profile: dict) -> dict:
    end = date.today() - timedelta(days=2)
    daily = []
    for i in range(56):
        day = end - timedelta(days=55 - i)
        r = _rng("gsc", domain, day.isoformat())
        clicks = int(180 + i * 2.2 + r.randint(-30, 30))
        daily.append({"day": day.isoformat(), "clicks": clicks, "impressions": clicks * r.randint(20, 32),
                      "position": round(r.uniform(11, 17), 2)})
    keywords = ranked_keywords(domain, profile)
    queries = [{"query": k["term"], "url": k["url"], "clicks": max(0, int(k["volume"] / (k["position"] + 2))),
                "impressions": k["volume"], "position": float(k["position"])} for k in keywords]
    pages_cur, pages_prev = [], []
    for k in keywords[:20]:
        r = _rng("page", domain, k["url"])
        cur = max(1, int(k["volume"] / (k["position"] + 2)))
        prev = int(cur * r.uniform(0.7, 1.5))
        pages_cur.append({"url": k["url"], "clicks": cur, "impressions": k["volume"] * 8, "position": float(k["position"])})
        pages_prev.append({"url": k["url"], "clicks": prev, "impressions": k["volume"] * 8,
                           "position": round(k["position"] * r.uniform(0.75, 1.1), 1)})
    return {"as_of": end.isoformat(), "daily": daily, "pages_current": pages_cur, "pages_previous": pages_prev, "queries": queries}


def ga4_sync(domain: str) -> dict:
    end = date.today() - timedelta(days=1)
    out = []
    for i in range(56):
        day = end - timedelta(days=55 - i)
        r = _rng("ga", domain, day.isoformat())
        sessions = 450 + i * 4 + r.randint(-60, 60)
        out.append({"day": day.isoformat(), "sessions": sessions, "organic_sessions": int(sessions * r.uniform(0.38, 0.5)),
                    "conversions": r.randint(4, 12)})
    return {"daily": out}
