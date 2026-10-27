"""Live "test connection" checks — a cheap, read-only call against each
provider with the stored credentials. Each returns (ok, detail)."""
import asyncio
import json
import smtplib
import time

import httpx

TIMEOUT = 15.0


async def _get(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        return await client.get(url, **kwargs)


def _verdict(resp: httpx.Response, ok_text: str) -> tuple[bool, str]:
    if resp.status_code == 200:
        return True, ok_text
    try:
        body = resp.json()
        message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error") or body.get("message") or body.get("detail")
    except ValueError:
        message = resp.text[:200]
    return False, f"HTTP {resp.status_code}: {message or 'request refused'}"


async def anthropic(c: dict) -> tuple[bool, str]:
    headers = {"x-api-key": c["api_key"], "anthropic-version": "2023-06-01"}
    if c.get("workspace_id"):
        headers["anthropic-workspace-id"] = c["workspace_id"]
    # A one-token message proves the key, the workspace and that the
    # account has credits — listing models succeeds without credits.
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json={
            "model": c.get("model") or "claude-sonnet-5-5", "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}],
        })
    return _verdict(resp, f"Key works and {c.get('model')} answered")


async def openai(c: dict) -> tuple[bool, str]:
    resp = await _get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {c['api_key']}"})
    return _verdict(resp, "Key accepted")


async def smtp(c: dict) -> tuple[bool, str]:
    def check():
        with smtplib.SMTP(c["host"], int(c.get("port") or 587), timeout=TIMEOUT) as server:
            if str(c.get("use_tls", "true")).lower() == "true":
                server.starttls()
            if c.get("username") and c.get("password"):
                server.login(c["username"], c["password"])
            server.noop()
    try:
        await asyncio.to_thread(check)
        return True, f"Connected to {c['host']} and signed in"
    except (smtplib.SMTPException, OSError) as exc:
        return False, str(exc)[:200]


async def twilio(c: dict) -> tuple[bool, str]:
    resp = await _get(f"https://api.twilio.com/2010-04-01/Accounts/{c['account_sid']}.json", auth=(c["account_sid"], c["auth_token"]))
    if resp.status_code == 200:
        return True, f"Account {resp.json().get('friendly_name', '')} ({resp.json().get('status', '')})"
    return _verdict(resp, "")


async def fcm_access_token(service_account_json: str) -> tuple[str, str]:
    """OAuth token for FCM HTTP v1 from a service account (RS256 JWT
    grant). Returns (access_token, project_id)."""
    import jwt  # PyJWT with the cryptography backend

    account = json.loads(service_account_json)
    now = int(time.time())
    assertion = jwt.encode({
        "iss": account["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
        "aud": account.get("token_uri", "https://oauth2.googleapis.com/token"), "iat": now, "exp": now + 3600,
    }, account["private_key"], algorithm="RS256")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(account.get("token_uri", "https://oauth2.googleapis.com/token"), data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion,
        })
    resp.raise_for_status()
    return resp.json()["access_token"], account["project_id"]


async def fcm(c: dict) -> tuple[bool, str]:
    try:
        _token, project = await fcm_access_token(c["service_account_json"])
        return True, f"Service account accepted for project {project}"
    except (ValueError, KeyError) as exc:
        return False, f"Service account JSON is not valid: {exc}"
    except httpx.HTTPError as exc:
        return False, f"Google refused the service account: {exc}"


async def meta(c: dict) -> tuple[bool, str]:
    resp = await _get(f"https://graph.facebook.com/v21.0/{c['page_id']}", params={"fields": "name", "access_token": c["page_access_token"]})
    return _verdict(resp, f"Page: {resp.json().get('name', '') if resp.status_code == 200 else ''}")


async def linkedin(c: dict) -> tuple[bool, str]:
    resp = await _get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": f"Bearer {c['access_token']}"})
    return _verdict(resp, "Token accepted")


async def x(c: dict) -> tuple[bool, str]:
    resp = await _get("https://api.x.com/2/users/me", headers={"Authorization": f"Bearer {c['access_token']}"})
    return _verdict(resp, f"Signed in as @{resp.json().get('data', {}).get('username', '') if resp.status_code == 200 else ''}")


async def stripe(c: dict) -> tuple[bool, str]:
    resp = await _get("https://api.stripe.com/v1/balance", auth=(c["api_key"], ""))
    return _verdict(resp, "Key accepted" + (" (test mode)" if c["api_key"].startswith(("sk_test", "rk_test")) else ""))


async def _google_token(service_account_json: str, scope: str) -> str:
    import jwt  # PyJWT with the cryptography backend

    account = json.loads(service_account_json)
    now = int(time.time())
    uri = account.get("token_uri", "https://oauth2.googleapis.com/token")
    assertion = jwt.encode({"iss": account["client_email"], "scope": scope, "aud": uri, "iat": now, "exp": now + 3600},
                           account["private_key"], algorithm="RS256")
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(uri, data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    resp.raise_for_status()
    return resp.json()["access_token"]


async def google_seo(c: dict) -> tuple[bool, str]:
    try:
        token = await _google_token(c["service_account_json"], "https://www.googleapis.com/auth/webmasters.readonly")
    except (ValueError, KeyError) as exc:
        return False, f"Service account JSON is not valid: {exc}"
    except httpx.HTTPError as exc:
        return False, f"Google refused the service account: {exc}"
    resp = await _get("https://www.googleapis.com/webmasters/v3/sites", headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 200:
        sites = [s.get("siteUrl") for s in resp.json().get("siteEntry", [])]
        return True, f"Search Console properties: {', '.join(sites) or 'none yet — add the service account as a user'}"
    return _verdict(resp, "")


async def dataforseo(c: dict) -> tuple[bool, str]:
    resp = await _get("https://api.dataforseo.com/v3/appendix/user_data", auth=(c["login"], c["password"]))
    if resp.status_code == 200 and resp.json().get("status_code") == 20000:
        money = ((resp.json().get("tasks") or [{}])[0].get("result") or [{}])[0].get("money") or {}
        return True, f"Account reachable (balance ${money.get('balance', '?')})"
    return _verdict(resp, "") if resp.status_code != 200 else (False, resp.json().get("status_message", "refused"))


async def wordpress(c: dict) -> tuple[bool, str]:
    resp = await _get(f"{c['base_url'].rstrip('/')}/wp-json/wp/v2/users/me", auth=(c["username"], c["app_password"]), params={"context": "edit"})
    if resp.status_code == 200 and not (resp.json().get("capabilities") or {}).get("publish_posts"):
        return False, "Signed in, but this user cannot publish posts"
    return _verdict(resp, f"Signed in as {resp.json().get('name', '') if resp.status_code == 200 else ''}")


async def cms_webhook(c: dict) -> tuple[bool, str]:
    import hashlib
    import hmac

    body = json.dumps({"event": "ping"}).encode()
    signature = "sha256=" + hmac.new(c["signing_secret"].encode(), body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(c["url"], content=body, headers={"Content-Type": "application/json", "X-VOYQI-Signature": signature})
    return (True, "The website answered the signed ping") if resp.status_code < 300 else _verdict(resp, "")


async def google_business_profile(c: dict) -> tuple[bool, str]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        token = await client.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "refresh_token", "client_id": c["client_id"], "client_secret": c["client_secret"], "refresh_token": c["refresh_token"]})
    if token.status_code != 200:
        return _verdict(token, "")
    resp = await _get("https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
                      headers={"Authorization": f"Bearer {token.json()['access_token']}"})
    return _verdict(resp, f"{len(resp.json().get('accounts', [])) if resp.status_code == 200 else 0} account(s) reachable")


async def perplexity(c: dict) -> tuple[bool, str]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post("https://api.perplexity.ai/chat/completions", headers={"Authorization": f"Bearer {c['api_key']}"},
                                 json={"model": "sonar", "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]})
    return _verdict(resp, "Key works")


async def youtube(c: dict) -> tuple[bool, str]:
    resp = await _get("https://www.googleapis.com/youtube/v3/search", params={"part": "snippet", "q": "test", "maxResults": 1, "key": c["api_key"]})
    return _verdict(resp, "Key works")


async def reddit(c: dict) -> tuple[bool, str]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post("https://www.reddit.com/api/v1/access_token", auth=(c["client_id"], c["client_secret"]),
                                 data={"grant_type": "client_credentials"}, headers={"User-Agent": "VOYQI-SEO-Research/1.0"})
    return _verdict(resp, "App credentials accepted") if resp.status_code != 200 or "access_token" in resp.json() else (False, "no token issued")


async def seo_inbox(c: dict) -> tuple[bool, str]:
    if len(c.get("token", "")) < 24:
        return False, "Use a token of at least 24 random characters"
    return True, "Token set — the inbound webhook will accept requests carrying it"


CHECKS = {"anthropic": anthropic, "openai": openai, "smtp": smtp, "twilio": twilio, "fcm": fcm,
          "meta": meta, "linkedin": linkedin, "x": x, "stripe": stripe, "google_seo": google_seo, "dataforseo": dataforseo,
          "wordpress": wordpress, "cms_webhook": cms_webhook, "google_business_profile": google_business_profile,
          "perplexity": perplexity, "youtube": youtube, "reddit": reddit, "seo_inbox": seo_inbox}


async def run(provider: str, config: dict) -> tuple[bool, str]:
    try:
        return await CHECKS[provider](config)
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach the provider: {type(exc).__name__}"
