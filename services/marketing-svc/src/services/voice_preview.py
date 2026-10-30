"""Hear a voice script before it's sent: text-to-speech through the
tenant's OpenAI integration (super admin → Integrations → OpenAI).

The call itself is read by Twilio's Polly voice, so the preview is a
close stand-in — same words, pacing and opening line, a comparable
voice — and is labelled as such in the console."""
import httpx

from src.core import integrations
from src.services.channel_options import VOICES

# The OpenAI voice closest to each Polly voice (gender and tone).
NEAREST = {"Polly.Joanna": "nova", "Polly.Matthew": "onyx", "Polly.Amy": "shimmer", "Polly.Brian": "fable", "Polly.Lupe": "nova",
           "Polly.Miguel": "echo", "Polly.Lea": "shimmer", "Polly.Mathieu": "echo", "Polly.Zeina": "alloy"}
OPENING = "This is an automated call from VOYQI."
MAX_CHARS = 2000


class PreviewUnavailable(Exception):
    pass


async def speak(tenant_id: str, text: str, voice: str, record: bool = False) -> bytes:
    """MP3 audio of the call as it will sound: the opening line, then the script."""
    config = await integrations.get(tenant_id, "openai")
    if not config or not config.get("api_key"):
        raise PreviewUnavailable("Connect OpenAI under super admin → Integrations to hear voice previews.")
    if voice not in VOICES:
        raise ValueError(f"voice must be one of {', '.join(VOICES)}")
    script = (OPENING + (" This call may be recorded for quality." if record else "") + " " + (text or "").strip())[:MAX_CHARS]
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {config['api_key']}"},
                                     json={"model": config.get("tts_model") or "gpt-4o-mini-tts", "voice": NEAREST[voice], "input": script, "response_format": "mp3"})
    except httpx.HTTPError as exc:
        raise PreviewUnavailable(f"OpenAI is unreachable ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise PreviewUnavailable(f"OpenAI HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.content
