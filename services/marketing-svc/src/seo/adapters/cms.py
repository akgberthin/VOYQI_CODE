"""Publishing and fixes on the website.

wordpress: the REST API with an Application Password (Users → Profile →
Application Passwords). Redirects need the free Redirection plugin.
webhook: any CMS or website builder — VOYQI POSTs a signed JSON change
request and the site applies it, answering {url, ref} or {applied, detail}."""
import hashlib
import hmac
import html
import json
import re
from urllib.parse import urlparse

from src.seo.adapters.base import ProviderError, ProviderNotConfigured, request


def markdown_to_html(md: str) -> str:
    """Enough Markdown for articles: headings, paragraphs, lists, links,
    bold/italic and tables pass through as text."""
    def inline(text: str) -> str:
        text = html.escape(text, quote=False)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{html.escape(m.group(2))}">{m.group(1)}</a>', text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        return re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)

    out, para, items, ordered = [], [], [], False

    def flush() -> None:
        nonlocal para, items
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para = []
        if items:
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{tag}>")
            items = []

    for line in (md or "").splitlines():
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        number = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif bullet or number:
            if para:
                flush()
            if items and ordered != bool(number):
                flush()
            ordered = bool(number)
            items.append((bullet or number).group(1))
        elif not stripped:
            flush()
        else:
            if items:
                flush()
            para.append(stripped)
    flush()
    return "\n".join(out)


def _strip_h1(md: str) -> str:
    """WordPress prints the title itself; drop a leading '# Title'."""
    return re.sub(r"^\s*#\s+[^\n]*\n", "", md or "", count=1)


# ── WordPress ─────────────────────────────────────────────────────

def _wp(creds: dict) -> tuple[str, tuple[str, str]]:
    missing = [k for k in ("base_url", "username", "app_password") if not creds.get(k)]
    if missing:
        raise ProviderNotConfigured(f"the 'wordpress' integration is missing {', '.join(missing)}")
    return creds["base_url"].rstrip("/"), (creds["username"], creds["app_password"])


async def wp_test(creds: dict) -> str:
    base, auth = _wp(creds)
    resp = await request("GET", f"{base}/wp-json/wp/v2/users/me", what="WordPress", auth=auth, params={"context": "edit"})
    caps = resp.json().get("capabilities") or {}
    if not caps.get("publish_posts"):
        raise ProviderError("this WordPress user cannot publish posts; use an Editor or Administrator")
    return f"Signed in to WordPress as {resp.json().get('name')}"


async def _wp_find(base: str, auth: tuple, url: str) -> tuple[str, dict] | None:
    slug = [p for p in urlparse(url).path.split("/") if p]
    slug = slug[-1] if slug else ""
    for kind in ("pages", "posts"):
        params = {"slug": slug} if slug else {}
        if not slug and kind == "posts":
            continue
        resp = await request("GET", f"{base}/wp-json/wp/v2/{kind}", what="WordPress", auth=auth,
                             params={**params, "context": "edit", "per_page": 5})
        for item in resp.json():
            if not slug or item.get("link", "").rstrip("/") == url.rstrip("/") or item.get("slug") == slug:
                return kind, item
    return None


async def wp_publish(creds: dict, item: dict) -> dict:
    base, auth = _wp(creds)
    body = {"title": item["title"], "content": markdown_to_html(_strip_h1(item["body_markdown"])), "status": "publish",
            "excerpt": item.get("meta_description", ""), "slug": item["slug"]}
    if item.get("refresh_of_url"):
        found = await _wp_find(base, auth, item["refresh_of_url"])
        if found is None:
            raise ProviderError(f"could not find the WordPress page behind {item['refresh_of_url']}")
        kind, existing = found
        body.pop("slug")
        resp = await request("POST", f"{base}/wp-json/wp/v2/{kind}/{existing['id']}", what="WordPress", auth=auth, json=body)
    else:
        kind = "pages" if item.get("kind") in ("landing", "programmatic") else "posts"
        resp = await request("POST", f"{base}/wp-json/wp/v2/{kind}", what="WordPress", auth=auth, json=body)
    data = resp.json()
    return {"url": data.get("link"), "ref": f"wp:{kind}:{data.get('id')}"}


async def wp_fix(creds: dict, fix_type: str, params: dict) -> str:
    base, auth = _wp(creds)
    if fix_type in ("add_redirect", "fix_redirect_chain", "make_redirect_permanent"):
        source = urlparse(params["from"]).path or "/"
        try:
            await request("POST", f"{base}/wp-json/redirection/v1/redirect", what="WordPress Redirection plugin", auth=auth, json={
                "url": source, "action_data": {"url": params["to"]}, "action_type": "url", "action_code": 301,
                "match_type": "url", "group_id": int(params.get("group_id") or 1)})
        except ProviderError as exc:
            raise ProviderError(f"{exc.detail} — redirects need the Redirection plugin on the WordPress site") from exc
        return f"301 redirect {source} → {params['to']}"
    if fix_type == "add_to_sitemap":
        return "WordPress lists every published page in its sitemap automatically"
    if fix_type == "add_alt_text":
        done = 0
        for src, alt in (params.get("alt_texts") or {}).items():
            name = re.sub(r"\.[a-z0-9]+$", "", src.rsplit("/", 1)[-1], flags=re.I)
            found = await request("GET", f"{base}/wp-json/wp/v2/media", what="WordPress", auth=auth, params={"search": name, "per_page": 5})
            match = next((m for m in found.json() if m.get("source_url", "").rsplit("/", 1)[-1] == src.rsplit("/", 1)[-1]), None)
            if match:
                await request("POST", f"{base}/wp-json/wp/v2/media/{match['id']}", what="WordPress", auth=auth, json={"alt_text": alt})
                done += 1
        if not done:
            raise ProviderError("none of the images were found in the WordPress media library")
        return f"alt text set on {done} image(s)"
    found = await _wp_find(base, auth, params["url"])
    if found is None:
        raise ProviderError(f"could not find the WordPress page behind {params['url']}")
    kind, existing = found
    if fix_type == "update_title":
        await request("POST", f"{base}/wp-json/wp/v2/{kind}/{existing['id']}", what="WordPress", auth=auth, json={"title": params["value"]})
        return f"title set to “{params['value']}”"
    if fix_type == "update_meta_description":
        await request("POST", f"{base}/wp-json/wp/v2/{kind}/{existing['id']}", what="WordPress", auth=auth, json={"excerpt": params["value"]})
        return "excerpt (used as the meta description by SEO plugins and themes) updated"
    if fix_type == "add_schema":
        content = (existing.get("content") or {}).get("raw", "")
        if "application/ld+json" in content:
            return "the page already carries structured data"
        block = f'\n<script type="application/ld+json">{json.dumps(params["jsonld"])}</script>'
        await request("POST", f"{base}/wp-json/wp/v2/{kind}/{existing['id']}", what="WordPress", auth=auth, json={"content": content + block})
        return f"{params.get('schema_type', 'structured data')} JSON-LD added to the page"
    raise ProviderError(f"the WordPress adapter does not apply '{fix_type}'")


# ── signed webhook ────────────────────────────────────────────────

def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _hook(creds: dict, payload: dict) -> dict:
    if not creds.get("url") or not creds.get("signing_secret"):
        raise ProviderNotConfigured("the 'cms_webhook' integration needs a url and a signing_secret")
    body = json.dumps(payload, default=str).encode()
    resp = await request("POST", creds["url"], what="the website webhook", content=body,
                         headers={"Content-Type": "application/json", "X-VOYQI-Signature": sign(creds["signing_secret"], body)})
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError("the website webhook did not answer JSON") from exc


async def hook_test(creds: dict) -> str:
    await _hook(creds, {"event": "ping"})
    return "the website webhook answered"


async def hook_publish(creds: dict, item: dict) -> dict:
    data = await _hook(creds, {"event": "publish", "content": {**item, "body_html": markdown_to_html(item["body_markdown"])}})
    if not data.get("url"):
        raise ProviderError("the website webhook did not return the published url")
    return {"url": data["url"], "ref": str(data.get("ref") or data["url"])}


async def hook_fix(creds: dict, fix_type: str, params: dict) -> str:
    data = await _hook(creds, {"event": "fix", "fix_type": fix_type, "params": params})
    if not data.get("applied"):
        raise ProviderError(data.get("detail") or "the website did not apply the change")
    return data.get("detail") or "applied by the website"
