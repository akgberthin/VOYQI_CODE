"""Built-in site crawler: reads the sitemap and follows internal links,
parses each page with the standard library and reports what search
engines would trip over. Core Web Vitals come from Google PageSpeed
Insights (works without a key at low volume; a key raises the quota).

Only hosts of the site's own domain are fetched, and never one that
resolves to a private or loopback address."""
import asyncio
import ipaddress
import json
import re
import socket
from collections import defaultdict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree

import httpx

from src.seo.adapters.base import ProviderError
from src.seo.constants import FIX_POLICY

USER_AGENT = "VOYQI-SEO-Crawler/1.0 (+https://voyqi.com/bot)"
CTA_WORDS = re.compile(r"\b(start|get|book|join|sign up|subscribe|buy|reserve|request|try|see plans|compare|apply|contact)\b", re.I)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.canonical: str | None = None
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.images: list[dict] = []
        self.links: list[dict] = []
        self.forms: list[int] = []
        self.buttons: list[str] = []
        self.jsonld: list[str] = []
        self.text_parts: list[str] = []
        self._stack: list[str] = []
        self._capture: str | None = None
        self._buf: list[str] = []
        self._in_form = False
        self._form_fields = 0
        self._link: dict | None = None
        self._script_type: str | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower():
            self.canonical = a.get("href")
        elif tag == "img":
            self.images.append({"src": a.get("src", ""), "alt": a.get("alt")})
        elif tag == "a":
            self._link = {"href": a.get("href", ""), "rel": a.get("rel", ""), "text": ""}
        elif tag == "form":
            self._in_form, self._form_fields = True, 0
        elif tag in ("input", "select", "textarea") and self._in_form:
            if a.get("type", "").lower() not in ("hidden", "submit", "button", "image", "reset"):
                self._form_fields += 1
        elif tag == "script":
            self._script_type = a.get("type", "").lower()
        if tag in ("title", "h1", "h2", "button") or (tag == "script" and self._script_type == "application/ld+json"):
            self._capture, self._buf = tag, []
        if tag in ("script", "style", "noscript"):
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._link is not None:
            self.links.append(self._link)
            self._link = None
        elif tag == "form" and self._in_form:
            self.forms.append(self._form_fields)
            self._in_form = False
        if self._capture == tag:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if tag == "title":
                self.title = text
            elif tag == "h1":
                self.h1.append(text)
            elif tag == "h2":
                self.h2.append(text)
            elif tag == "button":
                self.buttons.append(text)
            elif tag == "script":
                self.jsonld.append("".join(self._buf))
            self._capture = None
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag == "script":
            self._script_type = None

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)
        if self._link is not None:
            self._link["text"] += data
        if not self._stack:
            self.text_parts.append(data)


def parse_html(html: str) -> dict:
    p = PageParser()
    try:
        p.feed(html)
    except Exception:  # malformed markup: keep what was parsed
        pass
    text = re.sub(r"\s+", " ", " ".join(p.text_parts)).strip()
    schema_types: list[str] = []
    for block in p.jsonld:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in (data.get("@graph", [data]) if isinstance(data, dict) else data if isinstance(data, list) else []):
            t = node.get("@type") if isinstance(node, dict) else None
            schema_types.extend(t if isinstance(t, list) else [t] if t else [])
    ctas = [b for b in p.buttons if b] + [link["text"].strip() for link in p.links if CTA_WORDS.search(link["text"] or "")]
    return {
        "title": p.title, "meta_description": p.meta.get("description", ""), "robots": p.meta.get("robots", "").lower(),
        "viewport": bool(p.meta.get("viewport")), "canonical": p.canonical, "h1": p.h1, "h2": p.h2,
        "images": p.images, "links": p.links, "forms": p.forms, "ctas": [c for c in ctas if c][:20],
        "schema_types": schema_types, "text": text, "words": len(text.split()),
    }


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def on_site(url: str, domain: str) -> bool:
    host = _host(url)
    return host == domain or host == f"www.{domain}" or host.endswith(f".{domain}")


async def _is_public(host: str) -> bool:
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


_host_ok: dict[str, bool] = {}


async def _guard(url: str, domain: str) -> None:
    host = _host(url)
    if not on_site(url, domain):
        raise ProviderError(f"{url} is not on {domain}")
    if host not in _host_ok:
        _host_ok[host] = await _is_public(host)
    if not _host_ok[host]:
        raise ProviderError(f"{host} does not resolve to a public address")


async def fetch(client: httpx.AsyncClient, url: str, domain: str, max_hops: int = 5) -> dict:
    """GET with redirects followed by hand, so the chain is visible."""
    hops: list[dict] = []
    current = url
    for _ in range(max_hops + 1):
        await _guard(current, domain)
        try:
            resp = await client.get(current, follow_redirects=False)
        except httpx.HTTPError as exc:
            return {"url": url, "final_url": current, "status": 0, "error": type(exc).__name__, "hops": hops, "html": ""}
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
            nxt = urljoin(current, resp.headers["location"])
            hops.append({"from": current, "to": nxt, "status": resp.status_code})
            if not on_site(nxt, domain):
                return {"url": url, "final_url": nxt, "status": resp.status_code, "hops": hops, "html": "", "offsite": True}
            current = nxt
            continue
        html = resp.text if "html" in resp.headers.get("content-type", "") else ""
        return {"url": url, "final_url": current, "status": resp.status_code, "hops": hops, "html": html,
                "x_robots": resp.headers.get("x-robots-tag", "").lower()}
    return {"url": url, "final_url": current, "status": 0, "error": "redirect loop", "hops": hops, "html": ""}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=20.0, headers={"User-Agent": USER_AGENT})


async def fetch_page(domain: str, url: str) -> dict:
    """One page, parsed (used for content refreshes and conversion review)."""
    async with _client() as client:
        got = await fetch(client, url, domain)
    if got["status"] != 200:
        raise ProviderError(f"{url} answered {got['status'] or got.get('error')}")
    return {**parse_html(got["html"]), "url": got["final_url"]}


async def _sitemap_urls(client: httpx.AsyncClient, domain: str, base: str) -> list[str] | None:
    """Page URLs from /sitemap.xml (following one level of sitemap
    indexes), or None when there is no readable sitemap."""
    urls: list[str] = []
    found = False
    queue, seen = [urljoin(base, "/sitemap.xml")], set()
    while queue and len(seen) < 10:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            await _guard(sm, domain)
            resp = await client.get(sm, follow_redirects=True)
        except (httpx.HTTPError, ProviderError):
            continue
        if resp.status_code != 200:
            continue
        try:
            root = ElementTree.fromstring(resp.content)
        except ElementTree.ParseError:
            continue
        found = True
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
        if root.tag.endswith("sitemapindex"):
            queue.extend(locs)
        else:
            urls.extend(locs)
    return urls if found else None


async def pagespeed(url: str, api_key: str | None = None) -> dict | None:
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    field = (data.get("loadingExperience") or {}).get("metrics") or {}
    audits = (data.get("lighthouseResult") or {}).get("audits") or {}

    def lab(key: str) -> float | None:
        return (audits.get(key) or {}).get("numericValue")

    lcp = (field.get("LARGEST_CONTENTFUL_PAINT_MS") or {}).get("percentile") or lab("largest-contentful-paint")
    cls_raw = (field.get("CUMULATIVE_LAYOUT_SHIFT_SCORE") or {}).get("percentile")
    cls = cls_raw / 100 if cls_raw is not None else lab("cumulative-layout-shift")
    inp = (field.get("INTERACTION_TO_NEXT_PAINT") or {}).get("percentile")
    return {"lcp_ms": round(lcp) if lcp is not None else None, "cls": round(cls, 3) if cls is not None else None,
            "inp_ms": inp, "source": "field" if field else "lab"}


def _issue(category: str, code: str, severity: str, url: str, detail: str, evidence: dict | None = None,
           fix_type: str | None = None, fix_params: dict | None = None) -> dict:
    return {"category": category, "code": code, "severity": severity, "url": url, "detail": detail,
            "evidence": evidence or {}, "fix_type": fix_type, "fix_params": fix_params or {}}


def _closest(url: str, candidates: list[str]) -> str | None:
    want = set(re.findall(r"[a-z0-9]+", urlparse(url).path.lower()))
    best, best_score = None, 0.0
    for c in candidates:
        have = set(re.findall(r"[a-z0-9]+", urlparse(c).path.lower()))
        score = len(want & have) / len(want | have) if want | have else 0
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.3 else None


def analyse(domain: str, base: str, pages: dict[str, dict], sitemap: list[str] | None, vitals: dict[str, dict]) -> list[dict]:
    """Issues from crawled pages. Each issue may carry a fix the fix
    policy decides whether an agent may apply."""
    issues: list[dict] = []
    in_sitemap = set(sitemap or [])
    ok_pages = [u for u, p in pages.items() if p["status"] == 200 and not p["hops"]]
    titles, metas = defaultdict(list), defaultdict(list)
    if sitemap is None:
        issues.append(_issue("indexing", "no_sitemap", "warning", urljoin(base, "/sitemap.xml"),
                             "no XML sitemap was found at /sitemap.xml"))
    for url, page in pages.items():
        status, hops = page["status"], page["hops"]
        if status >= 400 or status == 0:
            linked_from = page.get("linked_from", 0)
            target = _closest(url, ok_pages)
            issues.append(_issue("crawl", "http_4xx" if 400 <= status < 500 else "http_5xx" if status >= 500 else "unreachable",
                                 "critical" if url in in_sitemap or linked_from else "warning", url,
                                 f"returns {status or page.get('error')}" + (" but is listed in the sitemap" if url in in_sitemap else "")
                                 + (f"; linked from {linked_from} page(s)" if linked_from else ""),
                                 {"status": status, "linked_from": linked_from},
                                 "add_redirect" if target and 400 <= status < 500 else None,
                                 {"from": url, "to": target, "status": 301} if target else None))
            continue
        if len(hops) >= 2:
            issues.append(_issue("redirect", "redirect_chain", "warning", url, f"{len(hops)} redirect hops before the final page",
                                 {"hops": hops}, "fix_redirect_chain", {"from": url, "to": page["final_url"], "status": 301}))
        elif hops and hops[0]["status"] in (302, 307):
            issues.append(_issue("redirect", "redirect_temporary", "warning", url, f"redirects with a temporary {hops[0]['status']}",
                                 {"hops": hops}, "make_redirect_permanent", {"from": url, "to": page["final_url"], "status": 301}))
        parsed = page.get("parsed")
        if not parsed:
            continue
        final = page["final_url"]
        noindex = "noindex" in parsed["robots"] or "noindex" in page.get("x_robots", "")
        if noindex and (url in in_sitemap or final in in_sitemap):
            issues.append(_issue("indexing", "noindex_in_sitemap", "critical", final,
                                 "the page is in the sitemap but tells search engines not to index it", {},
                                 "remove_noindex", {"url": final}))
        if noindex:
            continue
        if parsed["canonical"]:
            canon = urldefrag(urljoin(final, parsed["canonical"]))[0]
            if canon.rstrip("/") != final.rstrip("/"):
                issues.append(_issue("indexing", "canonical_mismatch", "warning", final, f"canonical points to a different URL: {canon}",
                                     {"canonical": canon}, "set_canonical", {"url": final, "canonical": final}))
        if not parsed["title"]:
            issues.append(_issue("content", "missing_title", "warning", final, "the page has no title tag", {},
                                 "update_title", {"url": final, "needs_generation": True}))
        else:
            titles[parsed["title"].lower()].append(final)
            if len(parsed["title"]) > 65:
                issues.append(_issue("content", "title_too_long", "info", final, f"title is {len(parsed['title'])} characters (over 65 get cut off)",
                                     {"title": parsed["title"]}, "update_title", {"url": final, "needs_generation": True}))
        if not parsed["meta_description"]:
            issues.append(_issue("content", "missing_meta_description", "warning", final, "the page has no meta description", {},
                                 "update_meta_description", {"url": final, "needs_generation": True}))
        else:
            metas[parsed["meta_description"].lower()].append(final)
        if not parsed["h1"]:
            issues.append(_issue("content", "missing_h1", "warning", final, "the page has no H1 heading"))
        elif len(parsed["h1"]) > 1:
            issues.append(_issue("content", "multiple_h1", "info", final, f"{len(parsed['h1'])} H1 headings (use one)"))
        no_alt = [i["src"] for i in parsed["images"] if not (i.get("alt") or "").strip()]
        if no_alt:
            issues.append(_issue("content", "images_missing_alt", "info", final, f"{len(no_alt)} image(s) without alt text",
                                 {"images": no_alt[:20]}, "add_alt_text", {"url": final, "images": no_alt[:20], "needs_generation": True}))
        if parsed["words"] < 200:
            issues.append(_issue("content", "thin_content", "info", final, f"only {parsed['words']} words of text"))
        path = urlparse(final).path.rstrip("/")
        if path == "" and "Organization" not in parsed["schema_types"]:
            issues.append(_issue("schema", "schema_missing", "warning", final, "a home page should carry Organization structured data",
                                 {}, "add_schema", {"url": final, "schema_type": "Organization"}))
        questions = [h for h in parsed["h2"] if h.endswith("?")]
        if len(questions) >= 3 and "FAQPage" not in parsed["schema_types"]:
            issues.append(_issue("schema", "schema_missing", "info", final, f"{len(questions)} question headings without FAQPage structured data",
                                 {"questions": questions[:10]}, "add_schema", {"url": final, "schema_type": "FAQPage"}))
        if sitemap and final not in in_sitemap and url not in in_sitemap:
            issues.append(_issue("indexing", "not_in_sitemap", "info", final, "an indexable page missing from the sitemap", {},
                                 "add_to_sitemap", {"url": final}))
        if not parsed["viewport"]:
            issues.append(_issue("indexing", "no_viewport", "warning", final, "no viewport meta tag: the page is not set up for phones"))
    for group, label, code in ((titles, "title", "duplicate_title"), (metas, "meta description", "duplicate_meta_description")):
        for _text, urls in group.items():
            if len(urls) > 1:
                for u in urls:
                    issues.append(_issue("content", code, "warning" if code == "duplicate_title" else "info", u,
                                         f"{len(urls)} pages share this {label}", {"pages": urls[:10]}))
    lcp_good, lcp_poor = FIX_POLICY["core_web_vitals"]["lcp_ms"]
    cls_good, cls_poor = FIX_POLICY["core_web_vitals"]["cls"]
    inp_good, inp_poor = FIX_POLICY["core_web_vitals"]["inp_ms"]
    for url, v in vitals.items():
        if v.get("lcp_ms") and v["lcp_ms"] > lcp_good:
            poor = v["lcp_ms"] > lcp_poor
            issues.append(_issue("speed", "lcp_poor" if poor else "lcp_slow", "critical" if poor else "warning", url,
                                 f"Largest Contentful Paint {v['lcp_ms']} ms (good is under {lcp_good})", {"lcp_ms": v["lcp_ms"]},
                                 "optimize_speed", {"focus": "lcp"}))
        if v.get("cls") is not None and v["cls"] > cls_good:
            poor = v["cls"] > cls_poor
            issues.append(_issue("speed", "cls_poor" if poor else "cls_high", "critical" if poor else "warning", url,
                                 f"Cumulative Layout Shift {v['cls']} (good is under {cls_good})", {"cls": v["cls"]},
                                 "optimize_speed", {"focus": "cls"}))
        if v.get("inp_ms") and v["inp_ms"] > inp_good:
            poor = v["inp_ms"] > inp_poor
            issues.append(_issue("speed", "inp_poor" if poor else "inp_slow", "critical" if poor else "warning", url,
                                 f"Interaction to Next Paint {v['inp_ms']} ms (good is under {inp_good})", {"inp_ms": v["inp_ms"]},
                                 "optimize_speed", {"focus": "inp"}))
    return issues


async def crawl(domain: str, config: dict, pagespeed_key: str | None = None) -> dict:
    """Crawls the site and returns {pages_scanned, issues, pages}."""
    max_pages = int(config.get("max_pages") or 60)
    base = config.get("start_url") or f"https://{domain}/"
    async with _client() as client:
        sitemap = await _sitemap_urls(client, domain, base)
        queue = [base, *(sitemap or [])]
        pages: dict[str, dict] = {}
        linked_from: dict[str, int] = defaultdict(int)
        sem = asyncio.Semaphore(int(config.get("concurrency") or 4))

        async def visit(url: str) -> None:
            async with sem:
                try:
                    got = await fetch(client, url, domain)
                except ProviderError as exc:
                    got = {"url": url, "final_url": url, "status": 0, "error": str(exc), "hops": [], "html": ""}
            got["parsed"] = parse_html(got.pop("html")) if got.get("html") else None
            pages[url] = got

        seen: set[str] = set()
        while queue and len(seen) < max_pages:
            batch = []
            while queue and len(seen) + len(batch) < max_pages:
                u = urldefrag(queue.pop(0))[0]
                if u in seen or not on_site(u, domain):
                    continue
                seen.add(u)
                batch.append(u)
            await asyncio.gather(*(visit(u) for u in batch))
            for u in batch:
                parsed = pages[u].get("parsed")
                for link in (parsed or {}).get("links", []):
                    href = urldefrag(urljoin(pages[u]["final_url"], link["href"]))[0]
                    if href.startswith("http") and on_site(href, domain):
                        linked_from[href] += 1
                        if href not in seen:
                            queue.append(href)
        for url, count in linked_from.items():
            if url in pages:
                pages[url]["linked_from"] = count
    if not pages or all(p["status"] == 0 for p in pages.values()):
        raise ProviderError(f"could not fetch any page of {domain}")
    vitals: dict[str, dict] = {}
    if config.get("pagespeed", True):
        targets = [base, *[u for u in (config.get("pagespeed_pages") or []) if on_site(u, domain)]][:3]
        results = await asyncio.gather(*(pagespeed(u, pagespeed_key) for u in targets))
        vitals = {u: r for u, r in zip(targets, results) if r}
    summary = {u: {"status": p["status"], "final_url": p["final_url"], "title": (p.get("parsed") or {}).get("title"),
                   "words": (p.get("parsed") or {}).get("words")} for u, p in pages.items()}
    return {"pages_scanned": len(pages), "issues": analyse(domain, base, pages, sitemap, vitals), "pages": summary, "vitals": vitals}


def page_signals(parsed: dict) -> dict[str, Any]:
    """What conversion review reads from a page."""
    text = parsed["text"]
    first_screen = text[:600]
    return {
        "cta_above_fold": bool(CTA_WORDS.search(first_screen)) or bool(parsed["ctas"] and parsed["ctas"][0] and parsed["ctas"][0] in first_screen),
        "ctas": parsed["ctas"], "max_form_fields": max(parsed["forms"], default=0),
        "trust": bool(re.search(r"\b(review|reviews|testimonial|rated|rating|trustpilot|stars?|members say|customers say)\b", text, re.I)),
        "pricing": bool(re.search(r"(\$\s?\d|€\s?\d|£\s?\d|\bper month\b|/mo\b|\bpricing\b|\bfrom \d)", text, re.I)),
        "viewport": parsed["viewport"], "title": parsed["title"], "h1": parsed["h1"][0] if parsed["h1"] else "",
    }
