"""Voice-script previews go through the OpenAI integration the super admin set up."""
import json

import httpx

from src.core import integrations
from src.services import voice_preview
from tests.conftest import TENANT_HEADERS

URL = "/api/v1/marketing/content/voice-preview"


async def test_without_openai_the_preview_says_what_to_set_up(client, monkeypatch):
    async def none(tenant_id, provider):
        return None

    monkeypatch.setattr(integrations, "get", none)
    r = await client.post(URL, json={"text": "Hi", "voice": "Polly.Joanna"}, headers=TENANT_HEADERS)
    assert r.status_code == 409 and "super admin" in r.json()["detail"]


async def test_preview_uses_the_tenants_key_and_the_nearest_voice(client, monkeypatch):
    async def creds(tenant_id, provider):
        return {"api_key": "sk-test", "tts_model": "gpt-4o-mini-tts"} if provider == "openai" else None

    sent = []
    real = httpx.AsyncClient

    def handler(request):
        sent.append(request)
        return httpx.Response(200, content=b"ID3fake-mp3")

    monkeypatch.setattr(integrations, "get", creds)
    monkeypatch.setattr(voice_preview.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = await client.post(URL, json={"text": "Your car is ready.", "voice": "Polly.Matthew", "record": True}, headers=TENANT_HEADERS)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3fake-mp3"
    body = json.loads(sent[0].content)
    assert sent[0].headers["Authorization"] == "Bearer sk-test" and body["voice"] == "onyx"
    assert body["input"].startswith("This is an automated call from VOYQI. This call may be recorded") and body["input"].endswith("Your car is ready.")
    assert (await client.post(URL, json={"text": "x", "voice": "Robot"}, headers=TENANT_HEADERS)).status_code == 422
