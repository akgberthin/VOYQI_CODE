"""Deterministic rules of the SEO engine — pure functions, no I/O.

Everything here gives the same answer for the same input, so the gates
built on it (autonomy, caps, link schemes, guardrails, thin content)
behave identically whether a person or an agent is asking."""
import math
import re
from collections import Counter
from typing import Any

from src.seo.constants import (
    ALLOWED_LINK_TYPES,
    ASSISTED_SAFE,
    BANNED_PHRASES,
    BLOCKED_LINK_TYPES,
    FIX_POLICY,
    GBP_CHECKLIST,
    SPAM_SCORE_BLOCK,
    TARGET_WORDS,
    TASK_TYPES,
)


class RuleError(ValueError):
    """Bad input to a rule; the API answers 422 with the message."""

    def __init__(self, message: str, **extra: Any):
        super().__init__(message)
        self.extra = extra


def clamp(value: float, low: float = 0, high: float = 100) -> float:
    return max(low, min(high, value))


# ── text ──────────────────────────────────────────────────────────

STOPWORDS = frozenset(
    "a an and are as at be by for from how in is it of on or that the this to vs what when where which who why "
    "with your you our we can do does".split()
)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def stems(text: str) -> set[str]:
    out = set()
    for w in words(text):
        if w in STOPWORDS or len(w) < 2:
            continue
        if len(w) > 3 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:120] or "page"


INJECTION_PATTERNS = [
    re.compile(p) for p in (
        r"ignore (all |any )?(previous|prior|above) (instructions|prompts)",
        r"disregard (the |your )?(system|previous)",
        r"you are now ",
        r"system prompt",
        r"reveal (your|the) (instructions|prompt)",
        r"developer message",
        r"<\s*/?\s*(system|assistant|tool)\s*>",
        r"act as (an? )?(admin|root|developer)",
    )
]


def looks_like_injection(text: str) -> bool:
    """Instruction-like text in something an agent will read (a research
    snippet, an emailed request). It is excluded or held, never obeyed."""
    low = (text or "").lower()
    return any(p.search(low) for p in INJECTION_PATTERNS)


# ── search intent ─────────────────────────────────────────────────

TRANSACTIONAL = ["buy", "price", "prices", "pricing", "cost", "costs", "cheap", "cheapest", "deal", "deals", "discount",
                 "order", "quote", "hire", "book", "booking", "sign up", "signup", "trial", "coupon", "for sale", "apply",
                 "subscribe", "get started"]
OFFERING = ["subscription", "subscriptions", "lease", "leasing", "rent", "rental", "rentals", "membership", "plans", "plan"]
COST_WORDS = ["cost", "costs", "price", "prices", "pricing", "how much", "cheap", "cheapest", "affordable"]
LOCAL = ["near me", "nearby", "open now", "in my area"]
COMMERCIAL = ["best", "top", "vs", "versus", "compare", "comparison", "review", "reviews", "alternatives", "alternative",
              "worth it", "pros and cons", "ranked", "which"]
QUESTION_STARTS = ["how", "what", "why", "when", "where", "who", "which", "can", "does", "do", "is", "are", "should", "will"]
INFORMATIONAL = ["guide", "tips", "tutorial", "learn", "meaning", "definition", "examples", "ideas", "checklist",
                 "explained", "basics"]


def _has(text: str, terms: list[str]) -> bool:
    return any(f" {t} " in f" {text} " for t in terms)


def is_question(query: str) -> bool:
    q = query.strip().lower()
    return q.endswith("?") or (q.split(" ")[0] if q else "") in QUESTION_STARTS


def classify_intent(query: str, brand_terms: list[str] | None = None) -> tuple[str, int]:
    """(intent, purchase intent 0-100) for a search query."""
    q = re.sub(r"\s+", " ", re.sub(r"[?!.,]", " ", query.lower())).strip()
    brands = [b.lower() for b in (brand_terms or []) if b]
    local, buy, compare = _has(q, LOCAL), _has(q, TRANSACTIONAL), _has(q, COMMERCIAL)
    if local and buy:
        return "transactional", 96
    if local:
        return "local", 82
    if is_question(query):
        return ("commercial", 75) if _has(q, COST_WORDS) else ("informational", 20)
    if buy and compare:
        return "transactional", 88
    if buy:
        return "transactional", 90
    if compare:
        return "commercial", 70
    if brands and any(b in q for b in brands):
        return "navigational", 45
    if _has(q, INFORMATIONAL):
        return "informational", 25
    if _has(q, OFFERING) or len(q.split(" ")) >= 2:
        return "commercial", 55 if _has(q, OFFERING) else 45
    return "informational", 35


def keyword_opportunity(volume: int | None, difficulty: int | None, purchase_intent: int, position: int | None) -> float:
    """Relative 0-100 ordering: demand (log scale), ease, buying intent,
    and headroom (already ranking 4-20 is the easiest win)."""
    demand = clamp(math.log10(max(1, volume or 0) + 1) * 25)
    ease = 100 - clamp(difficulty if difficulty is not None else 50)
    headroom = 100 if position is None or position > 20 else (90 if position > 3 else 30)
    return round(0.35 * demand + 0.25 * ease + 0.25 * purchase_intent + 0.15 * headroom, 1)


SERP_FORMATS = [
    ("listicle", re.compile(r"^\s*\d+\b|\b(best|top)\b", re.I)),
    ("how_to", re.compile(r"\b(how to|guide|tutorial|step[- ]by[- ]step)\b", re.I)),
    ("comparison", re.compile(r"\b(vs\.?|versus|compared|comparison|alternatives?)\b", re.I)),
    ("product_page", re.compile(r"\b(pricing|plans|buy|shop|get started|sign up|rent|subscribe)\b", re.I)),
    ("explainer", re.compile(r"\b(what is|what are|why|explained|meaning)\b", re.I)),
]
FORMAT_CONTENT = {
    "listicle": ("blog", "a ranked, honest comparison list with clear criteria", ["selection criteria", "pricing", "who each option suits"]),
    "how_to": ("blog", "a step-by-step guide that answers the task end to end", ["numbered steps", "common mistakes", "FAQ"]),
    "comparison": ("blog", "a fair side-by-side comparison", ["comparison table", "verdict by use case", "pricing"]),
    "product_page": ("landing", "a conversion-focused landing page", ["clear offer", "pricing or plans", "primary call to action", "trust signals"]),
    "explainer": ("pillar", "a clear explainer that defines the topic and answers follow-up questions", ["definition", "FAQ", "related topics"]),
}
INTENT_DEFAULT_FORMAT = {"transactional": "product_page", "commercial": "listicle", "informational": "how_to",
                         "local": "product_page", "navigational": "product_page"}


def search_intent(query: str, brand_terms: list[str], serp_items: list[dict]) -> dict:
    """What to write for a query: the intent, the format that already
    ranks (from search-result titles, when there are any), and what the
    page must cover."""
    intent, purchase = classify_intent(query, brand_terms)
    formats: Counter = Counter()
    features: Counter = Counter()
    for item in serp_items:
        for name, pattern in SERP_FORMATS:
            if pattern.search(item.get("title") or ""):
                formats[name] += 1
                break
        for feat in (item.get("signals") or {}).get("serp_features") or []:
            features[feat] += 1
    if formats and serp_items:
        dominant, count = formats.most_common(1)[0]
        confidence = round(min(0.95, 0.4 + count / len(serp_items) * 0.6), 2)
    else:
        dominant, confidence = INTENT_DEFAULT_FORMAT[intent], 0.35
    kind, angle, must = FORMAT_CONTENT[dominant]
    if features.get("local_pack"):
        must = [*must, "Google Business Profile and local landing page"]
    return {
        "query": query, "intent": intent, "purchase_intent": purchase, "dominant_format": dominant, "confidence": confidence,
        "evidence": {"serp_results": len(serp_items), "format_counts": dict(formats), "serp_features": dict(features)},
        "recommended_content": {"kind": kind, "angle": angle, "must_include": must},
    }


# ── conversion ────────────────────────────────────────────────────

GENERIC_CTAS = {"submit", "click here", "learn more", "read more", "more", "go", "send", "continue", "here"}
ACTION_VERBS = {"start", "get", "see", "book", "join", "compare", "build", "choose", "request", "view", "explore", "try",
                "reserve", "find", "pick", "check"}


def cta_strength(text: str) -> int:
    t = text.strip().lower()
    toks = [w for w in t.split() if w]
    if not toks:
        return 0
    score = 20
    score += 30 if toks[0] in ACTION_VERBS else 0
    score += 20 if 2 <= len(toks) <= 5 else (5 if len(toks) == 1 else 0)
    score += 20 if len(toks) >= 2 and not all(w in ("a", "the", "your", "now") for w in toks) else 0
    score += 10 if any(w in t for w in ("plan", "pricing", "quote", "membership", "demo", "trial", "offer", "price")) else 0
    score -= 40 if t in GENERIC_CTAS else 0
    return int(clamp(score))


def cta_variants(text: str, offer: str = "membership") -> list[str]:
    options = [f"See {offer} plans and pricing", f"Start your {offer}", f"Get my {offer} quote", f"Compare {offer} options"]
    return [o for o in options if o.lower() != text.strip().lower()][:3]


def two_proportion_p_value(n_a: int, conv_a: int, n_b: int, conv_b: int) -> float:
    """Two-sided p-value of a two-proportion z-test."""
    if n_a == 0 or n_b == 0:
        return 1.0
    pooled = (conv_a + conv_b) / (n_a + n_b)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 1.0
    z = abs(conv_a / n_a - conv_b / n_b) / se
    return math.erfc(z / math.sqrt(2))


# ── links ─────────────────────────────────────────────────────────

def link_block_reason(link_type: str, spam_score: int) -> str | None:
    """Why a link may never be pursued, or None. Link schemes are banned
    by search engines; there is no override."""
    if link_type in BLOCKED_LINK_TYPES:
        return f"'{link_type}' links are a link scheme: they are never pursued"
    if spam_score >= SPAM_SCORE_BLOCK:
        return f"spam score {spam_score} is at or above the {SPAM_SCORE_BLOCK} limit"
    return None


def prospect_score(relevance: float, authority: float, spam_score: float) -> float:
    return round(clamp(0.5 * relevance + 0.35 * clamp(authority) - 0.3 * spam_score), 1)


OPT_OUT_RE = re.compile(r"(unsubscribe|opt[- ]?out|reply (with )?stop|no longer wish)", re.I)
LINK_SCHEME_RE = re.compile(r"\b(pay|paid|\$\d+|fee|exchange links?|link swap|in return for a link)\b")


def outreach_findings(subject: str, body: str) -> list[str]:
    findings = []
    text = f"{subject} {body}".lower()
    for phrase in BANNED_PHRASES:
        if phrase in text:
            findings.append(f'contains flagged claim language: "{phrase}"')
    if not OPT_OUT_RE.search(body or ""):
        findings.append("missing opt-out language (required for commercial email)")
    if LINK_SCHEME_RE.search(text):
        findings.append("offers payment or a link exchange: that is a link scheme")
    return findings


def valid_link_type(link_type: str) -> bool:
    return link_type in ALLOWED_LINK_TYPES or link_type in BLOCKED_LINK_TYPES


DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$")


def normalize_domain(value: str) -> str:
    d = (value or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    return d.split("/")[0]


def domain_of(url: str) -> str:
    host = url.split("//", 1)[1] if "//" in url else url
    host = re.split(r"[/?#]", host)[0].lower()
    return host[4:] if host.startswith("www.") else host


# ── autonomy ──────────────────────────────────────────────────────

def autonomy(site: Any, task_type: str, injection: bool = False) -> tuple[bool, str]:
    """(may run unattended, why) for a task type on a site."""
    if injection:
        return False, "the request contained instruction-like text, so a person must approve it"
    override = (site.autonomy_overrides or {}).get(task_type)
    if override == "approval":
        return False, f"'{task_type}' is set to require approval for this site"
    if site.autopilot_mode == "off":
        return False, "autopilot is off for this site"
    if TASK_TYPES[task_type][1] == "approval" and override != "auto":
        return False, f"'{task_type}' always needs a person's approval unless explicitly overridden"
    if override == "auto":
        return True, "explicitly set to run unattended for this site"
    if site.autopilot_mode == "assisted" and task_type not in ASSISTED_SAFE:
        return False, (f"assisted mode only runs analysis unattended; '{task_type}' changes the site or sends messages, "
                       "which needs autopilot mode")
    return True, "allowed by this site's autopilot policy"


def task_priority(impact: float, effort: float, confidence: float) -> float:
    return round(clamp(impact) * clamp(confidence, 0, 1) / max(1, min(5, effort)), 2)


# ── secrets ───────────────────────────────────────────────────────

SECRET_KEY_RE = re.compile(r"(secret|token|passw|api[_-]?key|authorization|bearer|private[_-]?key|credential(?!_ref))", re.I)
SECRET_VALUE_RE = re.compile(r"(sk-[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{12,}|AKIA[0-9A-Z]{12,}|-----BEGIN)")


def secret_in_config(value: Any, path: str = "") -> str | None:
    """Integration config holds settings, never credentials: those stay
    in the encrypted store and are referenced by credential_ref."""
    if isinstance(value, list):
        for item in value:
            found = secret_in_config(item, path)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                return (f"config key '{path}{key}' looks like a credential: store only a credential_ref "
                        "(a pointer into your secret manager), never the secret")
            found = secret_in_config(item, f"{path}{key}.")
            if found:
                return found
        return None
    if isinstance(value, str) and SECRET_VALUE_RE.search(value):
        return f"a value under '{path.rstrip('.')}' looks like a secret: store only a credential_ref"
    return None


def looks_like_secret(value: str) -> bool:
    return bool(SECRET_VALUE_RE.search(value or ""))


# ── emailed requests ──────────────────────────────────────────────

URL_RE = re.compile(r"https?://[^\s)>\]]+")
TOPIC_RE = re.compile(r"(?:about|on|for|called|titled|regarding|around)\s+[\"“']?([^\n.\"”']{3,120})", re.I)
REQUEST_INTENTS = [
    (re.compile(r"\b(write|create|draft|publish|add)\b.{0,40}\b(blog|article|post|guide|page)\b", re.I), "content_create", "Create content"),
    (re.compile(r"\b(refresh|update|rewrite|improve|revamp)\b.{0,40}\b(blog|article|post|page|content)\b", re.I), "content_refresh", "Refresh content"),
    (re.compile(r"\b(audit|crawl|technical|site speed|core web vitals|page speed)\b", re.I), "technical_audit", "Technical audit"),
    (re.compile(r"\bredirects?\b", re.I), "redirect_fix", "Fix redirects"),
    (re.compile(r"\b(schema|structured data|rich results?)\b", re.I), "schema_implementation", "Add structured data"),
    (re.compile(r"\b(chatgpt|perplexity|gemini|ai (answers?|search|overviews?|visibility)|generative engine|geo)\b", re.I), "geo_check", "Check AI-answer visibility"),
    (re.compile(r"\b(backlinks?|link[- ]building|get (us )?links?|outreach)\b", re.I), "link_prospect", "Find link prospects"),
    (re.compile(r"\b(conversion|cta|call to action|landing page|convert)\b", re.I), "cro_analysis", "Conversion review"),
    (re.compile(r"\b(google business|gbp|google maps|local seo|map pack|local pack)\b", re.I), "gbp_optimize", "Optimize Business Profile"),
    (re.compile(r"\b(location pages?|programmatic|city pages?|landing pages? at scale)\b", re.I), "pseo_batch", "Programmatic pages"),
    (re.compile(r"\b(keyword research|keywords?)\b", re.I), "keyword_research", "Keyword research"),
    (re.compile(r"\b(competitors?|competitor analysis)\b", re.I), "competitor_research", "Competitor research"),
    (re.compile(r"\b(report|update me|progress)\b", re.I), "report", "Progress report"),
]
REQUESTABLE = sorted({t for _p, t, _l in REQUEST_INTENTS})


def parse_request(subject: str, body: str, site_domain: str) -> dict:
    """What an emailed request asks for: the task types, a topic, and
    only those URLs on the site's own domain."""
    text = f"{subject}\n{body}"
    intents, seen = [], set()
    for pattern, task_type, label in REQUEST_INTENTS:
        if pattern.search(text) and task_type not in seen:
            seen.add(task_type)
            intents.append({"type": task_type, "label": label})
    if {"content_refresh", "content_create"} <= seen and re.search(r"\b(refresh|update|rewrite|revamp)\b", text, re.I):
        intents = [i for i in intents if i["type"] != "content_create"]
    topic = None
    match = TOPIC_RE.search(URL_RE.sub(" ", text))
    if match:
        topic = re.split(r"\s+(?:and|then|also|plus|but|so that)\s+|[,;:\n]|\s+&\s+", match.group(1).strip(), flags=re.I)[0]
        topic = re.sub(r"\s+", " ", topic).strip().strip(" .,:;") or None
    urls = [u.rstrip(".,;") for u in URL_RE.findall(text)]
    own = [u for u in urls if re.match(rf"^https?://(www\.)?{re.escape(site_domain)}(/|$)", u, re.I)]
    return {"intents": intents, "topic": topic, "urls": own, "ignored_urls": len(urls) - len(own)}


# ── structured data and redirects ─────────────────────────────────

SCHEMA_REQUIRED = {
    "Organization": ["name", "url"],
    "LocalBusiness": ["name", "address", "telephone"],
    "Product": ["name"],
    "Article": ["headline", "datePublished", "author"],
    "FAQPage": ["mainEntity"],
    "BreadcrumbList": ["itemListElement"],
    "Vehicle": ["name"],
}


def _present(v: Any) -> bool:
    if v is None or v is False or v == 0 or v == "":
        return False
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def build_schema(schema_type: str, entity: dict) -> dict:
    if schema_type not in SCHEMA_REQUIRED:
        raise RuleError(f"unsupported schema type '{schema_type}' (supported: {sorted(SCHEMA_REQUIRED)})")
    missing = [f for f in SCHEMA_REQUIRED[schema_type]
               if not _present(entity.get(f)) and not (f == "mainEntity" and _present(entity.get("questions")))]
    if schema_type == "BreadcrumbList" and not _present(entity.get("itemListElement")) and _present(entity.get("crumbs")):
        missing = []
    if missing:
        raise RuleError(f"{schema_type} needs: {', '.join(missing)}", missing=missing)
    out: dict[str, Any] = {"@context": "https://schema.org", "@type": schema_type}
    if schema_type == "FAQPage":
        out["mainEntity"] = [{"@type": "Question", "name": q["question"], "acceptedAnswer": {"@type": "Answer", "text": q["answer"]}}
                             for q in (entity.get("questions") or entity.get("mainEntity"))]
    elif schema_type == "BreadcrumbList":
        out["itemListElement"] = ([{"@type": "ListItem", "position": i + 1, "name": c["name"], "item": c["url"]}
                                   for i, c in enumerate(entity["crumbs"])] if _present(entity.get("crumbs")) else entity["itemListElement"])
    elif schema_type == "Article":
        out["headline"], out["datePublished"] = entity["headline"], entity["datePublished"]
        author = entity["author"]
        out["author"] = {"@type": "Person", "name": author} if isinstance(author, str) else author
        for key in ("dateModified", "image", "description"):
            if _present(entity.get(key)):
                out[key] = entity[key]
    elif schema_type == "LocalBusiness":
        address = entity["address"]
        out["name"], out["telephone"] = entity["name"], entity["telephone"]
        out["address"] = {"@type": "PostalAddress", **address} if isinstance(address, dict) else address
        for key in ("url", "openingHours", "priceRange", "image", "geo"):
            if _present(entity.get(key)):
                out[key] = entity[key]
    else:
        for key, value in entity.items():
            if value is None or value == "" or (isinstance(value, list) and not value):
                continue
            out[key] = value
    return out


def validate_schema(jsonld: Any) -> list[str]:
    import json

    data = jsonld
    if isinstance(jsonld, str):
        try:
            data = json.loads(jsonld)
        except ValueError:
            return ["not valid JSON"]
    problems: list[str] = []
    nodes = (data.get("@graph") or [data]) if isinstance(data, dict) else []
    for node in nodes:
        if "@context" not in node and "@graph" not in data:
            problems.append("missing @context")
        types = node.get("@type")
        for t in (types if isinstance(types, list) else [types]):
            if not t:
                problems.append("missing @type")
            for field in SCHEMA_REQUIRED.get(t, []):
                if not _present(node.get(field)):
                    problems.append(f"{t}: missing required field '{field}'")
    return problems


def resolve_redirects(rules: list) -> dict:
    """Flattens redirect chains to one hop and reports loops."""
    mapping: dict[str, str] = {}
    for rule in rules:
        if isinstance(rule, dict):
            src, dst = rule.get("from"), rule.get("to")
        else:
            src, dst = (list(rule) + [None, None])[:2]
        if src and dst:
            mapping[src] = dst
    resolved, loops, seen_loops = [], [], set()
    for start in mapping:
        path, target = [start], mapping[start]
        while target in mapping and target not in path:
            path.append(target)
            target = mapping[target]
        if target in path:
            cycle = path[path.index(target):]
            key = "\0".join(sorted(cycle))
            if key not in seen_loops:
                seen_loops.add(key)
                loops.append([*cycle, target])
            continue
        resolved.append({"from": start, "to": target, "hops": len(path), "status": 301})
    resolved.sort(key=lambda r: r["from"])
    return {"resolved": resolved, "loops": loops, "flattened": sum(1 for r in resolved if r["hops"] > 1)}


def fix_plan_for(fix_type: str | None, params: dict) -> dict | None:
    if not fix_type:
        return None
    if fix_type in FIX_POLICY["auto_apply"]:
        return {"fix_type": fix_type, "params": params, "risk": "low", "auto_applicable": True,
                "reason": "low-risk, reversible fix on the auto-apply list"}
    if fix_type in FIX_POLICY["never_auto"]:
        return {"fix_type": fix_type, "params": params, "risk": "high" if fix_type in ("remove_noindex", "delete_page", "block_indexing", "update_robots") else "medium",
                "auto_applicable": False, "reason": "this change type is never applied without a person's approval"}
    # Anything not on either list (speed work, for example) changes how
    # the site is built: a person decides.
    return {"fix_type": fix_type, "params": params, "risk": "medium", "auto_applicable": False,
            "reason": "medium-risk change: needs a person's approval"}


# ── templates (programmatic pages) ────────────────────────────────

def render_template(template: str, row: dict) -> str:
    """{slot} is replaced from the row; {{#slot}}…{{/slot}} keeps its
    text only when the slot has a value."""
    def val(key: str) -> str:
        v = row.get(key)
        return str(v if v is not None else "").strip()

    out = re.sub(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", lambda m: m.group(2) if val(m.group(1)) else "", template or "", flags=re.S)
    return re.sub(r"\{(\w+)\}", lambda m: val(m.group(1)), out)


def shingles(text: str, size: int = 3) -> set[tuple]:
    toks = words(text)
    return {tuple(toks[i:i + size]) for i in range(max(0, len(toks) - size + 1))}


def uniqueness(text: str, siblings: list[str]) -> tuple[float, float]:
    """(share of this page's 3-word phrases no sibling has, highest
    similarity to any one sibling)."""
    mine = shingles(text)
    if not mine:
        return 0.0, 0.0
    others = [shingles(s) for s in siblings if s]
    union = set().union(*others) if others else set()
    unique_ratio = round(len(mine - union) / len(mine), 3)
    max_sim = round(max((jaccard(mine, o) for o in others), default=0.0), 3)
    return unique_ratio, max_sim


# ── AI answers ────────────────────────────────────────────────────

POSITIVE = ["best", "recommended", "top", "excellent", "great", "trusted", "leading", "reliable", "popular", "flexible",
            "praised", "highly rated", "standout"]
NEGATIVE = ["avoid", "poor", "worst", "complaints", "unreliable", "expensive", "scam", "lawsuit", "disappointing",
            "hidden fees", "limited", "not recommended"]


def _positions(text: str, term: str) -> list[int]:
    return [m.start() for m in re.finditer(rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])", text)]


def analyze_answer(answer: str, citations: list[str], brand_terms: list[str], domain: str, competitors: list[dict]) -> dict:
    """Whether an AI answer names the brand (and in what order among
    competitors), cites the site, and how it talks about the brand."""
    low = (answer or "").lower()
    brand_hits = [p for term in [*(brand_terms or []), domain] if term for p in _positions(low, term)]
    competitor_first: dict[str, int] = {}
    for comp in competitors:
        for name in {comp.get("name") or "", comp["domain"], comp["domain"].split(".")[0]}:
            if name:
                hits = _positions(low, name)
                if hits:
                    competitor_first[comp["domain"]] = min(competitor_first.get(comp["domain"], 10**9), *hits)
    mentioned = bool(brand_hits)
    position = None
    if mentioned:
        order = sorted([("__brand__", min(brand_hits)), *competitor_first.items()], key=lambda x: x[1])
        position = [k for k, _ in order].index("__brand__") + 1
    cited = [domain_of(c) for c in citations or []]
    window = ""
    if mentioned:
        for term in [*(brand_terms or []), domain]:
            m = re.search(rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])", low) if term else None
            if m:
                window = low[max(0, m.start() - 120): m.end() + 160]
                break
    pos, neg = sum(1 for w in POSITIVE if w in window), sum(1 for w in NEGATIVE if w in window)
    return {
        "brand_mentioned": mentioned, "mention_position": position,
        "domain_cited": any(c == domain or c.endswith("." + domain) for c in cited), "cited_domains": cited,
        "competitors_mentioned": [d for d, _ in sorted(competitor_first.items(), key=lambda x: x[1])],
        "sentiment": "negative" if neg > pos else ("positive" if pos > neg else "neutral"),
    }


def score_checks(checks: list) -> dict:
    n = len(checks)
    if not n:
        return {"checks": 0, "mention_rate": None, "citation_rate": None, "avg_position": None, "score": None}
    mentioned = [c for c in checks if c.brand_mentioned]
    mention_rate = len(mentioned) / n
    citation_rate = sum(1 for c in checks if c.domain_cited) / n
    positions = [c.mention_position for c in mentioned if c.mention_position]
    inverse = sum(1 / p for p in positions) / len(positions) if positions else 0
    return {
        "checks": n, "mention_rate": round(mention_rate, 3), "citation_rate": round(citation_rate, 3),
        "avg_position": round(sum(positions) / len(positions), 2) if positions else None,
        "score": round(100 * (0.5 * mention_rate + 0.35 * citation_rate + 0.15 * inverse), 1),
    }


# ── content quality and guardrail ─────────────────────────────────

FINANCE_RE = re.compile(r"\b(apr|financing|finance|credit|loan|interest rate|monthly payment)\b", re.I)


def guardrail(text: str) -> list[str]:
    """Claims a person must clear before anything is published."""
    low = (text or "").lower()
    findings = [f'contains flagged claim language: "{p}"' for p in BANNED_PHRASES if p in low]
    if FINANCE_RE.search(low) and "subject to credit approval" not in low:
        findings.append("mentions financing or credit without saying it is subject to credit approval")
    return findings


def content_quality(kind: str, keyword: str, title: str, meta: str, body: str) -> tuple[int, list[dict]]:
    """(SEO score 0-100, findings). Each finding costs its weight."""
    findings: list[dict] = []

    def add(code: str, detail: str, weight: int) -> None:
        findings.append({"code": code, "detail": detail, "weight": weight})

    kw = (keyword or "").strip().lower()
    text_words = words(body)
    n = len(text_words)
    if not 30 <= len(title or "") <= 65:
        add("title_length", f"title is {len(title or '')} characters; aim for 30-65", 8)
    if kw and kw not in (title or "").lower():
        add("keyword_in_title", f"the title does not contain '{kw}'", 10)
    if not 70 <= len(meta or "") <= 160:
        add("meta_length", f"meta description is {len(meta or '')} characters; aim for 70-160", 8)
    h1 = re.search(r"^#\s+(.+)$", body or "", re.M)
    if not h1:
        add("missing_h1", "the body has no top-level heading", 8)
    elif kw and kw not in h1.group(1).lower():
        add("keyword_in_h1", f"the main heading does not contain '{kw}'", 6)
    sections = re.findall(r"^##\s+", body or "", re.M)
    if len(sections) < (2 if kind in ("faq", "landing") else 3):
        add("few_sections", f"{len(sections)} section heading(s); break the piece into clear sections", 6)
    target = TARGET_WORDS.get(kind, 800)
    if n < 0.7 * target:
        add("too_short", f"{n} words; a {kind} piece needs about {target}", 12)
    if kw and n:
        kw_words = len(kw.split())
        occurrences = len(re.findall(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", (body or "").lower()))
        density = occurrences * kw_words / n * 100
        if density < 0.4 or density > 2.5:
            add("keyword_density", f"keyword density is {density:.1f}%; keep it between 0.4% and 2.5% "
                                   "(below reads as off-topic, above as stuffing)", 8)
        first_para = next((p for p in re.split(r"\n\s*\n", body or "") if p.strip() and not p.lstrip().startswith("#")), "")
        if kw not in first_para.lower():
            add("keyword_in_intro", "the first paragraph does not mention the keyword", 5)
    links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", body or "")
    internal = [u for u in links if u.startswith("/") or not u.startswith("http")]
    if len(internal) < 2:
        add("internal_links", f"{len(internal)} internal link(s); link at least 2 related pages", 6)
    if kind in ("blog", "pillar") and not [u for u in links if u.startswith("http")]:
        add("no_sources", "no link to an outside source; cite where facts come from", 3)
    if kind == "faq" and "?" not in (body or ""):
        add("faq_without_questions", "an FAQ piece should answer questions as questions", 6)
    sentences = [s.strip().lower() for s in re.split(r"[.!?]\s+", body or "") if len(s.split()) >= 4]
    if sentences:
        repeated = len(sentences) - len(set(sentences))
        if repeated / len(sentences) > 0.15:
            add("repetitive", f"{repeated} of {len(sentences)} sentences repeat; write each point once", 15)
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len > 25:
            add("long_sentences", f"sentences average {avg_len:.0f} words; aim for under 25", 4)
    score = int(clamp(100 - sum(f["weight"] for f in findings)))
    return score, findings


# ── local ─────────────────────────────────────────────────────────

def gbp_score(location: Any) -> tuple[int, list[dict]]:
    """Business Profile completeness against the checklist."""
    p = location.profile or {}
    address = location.address or {}
    done = {
        "name": bool(location.name), "primary_category": bool(location.categories),
        "additional_categories": len(location.categories or []) >= 2,
        "address": bool(address.get("street") and address.get("city")), "phone": bool(location.phone),
        "website": bool(location.website), "hours": len(location.hours or {}) >= 5,
        "special_hours": bool(p.get("special_hours")),
        "description": len(p.get("description") or "") >= 250, "services": len(location.services or []) >= 2,
        "photos": (p.get("photos_count") or 0) >= 10, "logo": bool(p.get("has_logo")), "cover": bool(p.get("has_cover")),
        "posts": (p.get("posts_last_7d") or 0) >= 1, "qa": (p.get("qa_unanswered") or 0) == 0,
        "reviews": (p.get("reviews_count") or 0) >= 10 and (p.get("reviews_avg") or 0) >= 4.0,
        "review_response": (p.get("review_response_rate") or 0) >= 0.8,
    }
    total = sum(i["weight"] for i in GBP_CHECKLIST)
    got = sum(i["weight"] for i in GBP_CHECKLIST if done[i["item"]])
    return round(got / total * 100), [dict(i) for i in GBP_CHECKLIST if not done[i["item"]]]


def nap_compare(location: Any, citation: dict) -> dict:
    """One directory listing against the location's own name, address
    and phone."""
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())

    address = location.address or {}
    own = {"name": location.name, "street": address.get("street", ""), "city": address.get("city", ""),
           "postal": address.get("postal", ""), "phone": re.sub(r"\D", "", location.phone or "")[-10:]}
    theirs = {"name": citation.get("name", ""), "street": citation.get("street", ""), "city": citation.get("city", ""),
              "postal": citation.get("postal", ""), "phone": re.sub(r"\D", "", citation.get("phone", "") or "")[-10:]}
    mismatched = [k for k in own if theirs[k] and own[k] and norm(theirs[k]) != norm(own[k])]
    missing = [k for k in own if own[k] and not theirs[k]]
    return {"directory": citation.get("directory", "unknown"), "consistent": not mismatched and not missing,
            "mismatched": mismatched, "missing": missing}
