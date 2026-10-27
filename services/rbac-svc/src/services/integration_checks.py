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


CHECKS = {"anthropic": anthropic, "openai": openai, "smtp": smtp, "twilio": twilio, "fcm": fcm,
          "meta": meta, "linkedin": linkedin, "x": x, "stripe": stripe}


async def run(provider: str, config: dict) -> tuple[bool, str]:
    try:
        return await CHECKS[provider](config)
    except httpx.HTTPError as exc:
        return False, f"Couldn't reach the provider: {type(exc).__name__}"
