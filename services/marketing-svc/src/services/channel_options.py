"""Per-channel message settings stored on a draft (`options`) and handed
to notification-svc with every delivery, plus the personalisation tokens
a message may use.

email  preview_text, from_name, reply_to
sms    append_stop (default true: "Reply STOP to opt out." is added unless
       the message already says STOP — TCPA/CTIA), sender_id (alphanumeric
       sender, where the carrier allows it), media_url (MMS image, https)
push   deep_link, notification_type, platforms (ios / android / web: only
       contacts whose `platform` attribute is one of them)
voice  voice, language, max_attempts (1-3), retry_hours (1/2/4/8/24),
       record (the call says it may be recorded)
"""
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUSH_TYPES = ("promotional", "transactional", "ai_proactive")
PUSH_PLATFORMS = ("ios", "android", "web")
SENDER_RE = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9 ]{1,11}$|^\+[1-9]\d{7,14}$")
# Amazon Polly voices Twilio <Say> supports, by the language they speak.
VOICES = {
    "Polly.Joanna": "en-US", "Polly.Matthew": "en-US", "Polly.Amy": "en-GB", "Polly.Brian": "en-GB",
    "Polly.Lupe": "es-US", "Polly.Miguel": "es-US", "Polly.Lea": "fr-FR", "Polly.Mathieu": "fr-FR", "Polly.Zeina": "arb",
}
LANGUAGES = ("en-US", "en-GB", "es-US", "fr-FR", "arb")
STOP_LINE = "Reply STOP to opt out."
RETRY_HOURS = (1, 2, 4, 8, 24)

# {token}: the contact field or attribute that fills it.
TOKENS = {"first_name": "first name", "name": "name", "city": "city", "class": "mobility class", "hub": "home hub",
          "vehicle": "vehicle", "gu_balance": "Growth Units balance"}


class InvalidOptions(ValueError):
    pass


def validate(channel: str, options: dict | None) -> dict:
    """The options this channel accepts, cleaned — or InvalidOptions."""
    o = dict(options or {})
    allowed = {"email": {"preview_text", "from_name", "reply_to"}, "sms": {"append_stop", "sender_id", "media_url"}, "push": {"deep_link", "notification_type", "platforms"},
               "voice": {"voice", "language", "max_attempts", "retry_hours", "record"}}.get(channel, set())
    unknown = set(o) - allowed
    if unknown:
        raise InvalidOptions(f"{channel} messages don't take {', '.join(sorted(unknown))}")
    out: dict = {}
    for key, value in o.items():
        if value in (None, ""):
            continue
        if key == "platforms":
            if not isinstance(value, list) or not value or any(p not in PUSH_PLATFORMS for p in value):
                raise InvalidOptions(f"platforms must be a list of {', '.join(PUSH_PLATFORMS)}")
            out[key] = sorted(set(value))
            continue
        if key == "max_attempts":
            if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3:
                raise InvalidOptions("max_attempts must be 1, 2 or 3")
            out[key] = value
            continue
        if key == "retry_hours":
            if value not in RETRY_HOURS or isinstance(value, bool):
                raise InvalidOptions(f"retry_hours must be one of {', '.join(map(str, RETRY_HOURS))}")
            out[key] = value
            continue
        if key in ("append_stop", "record"):
            if not isinstance(value, bool):
                raise InvalidOptions(f"{key} must be true or false")
            out[key] = value
            continue
        if not isinstance(value, str):
            raise InvalidOptions(f"{key} must be text")
        value = " ".join(value.split())
        limits = {"preview_text": 200, "from_name": 80, "reply_to": 320, "deep_link": 512, "media_url": 1024}
        if key in limits and len(value) > limits[key]:
            raise InvalidOptions(f"{key} is longer than {limits[key]} characters")
        if key == "sender_id" and not SENDER_RE.match(value):
            raise InvalidOptions("sender_id must be 1-11 letters/digits (with at least one letter) or a +E.164 number")
        if key == "media_url" and not value.startswith("https://"):
            raise InvalidOptions("media_url must be an https:// address the carrier can download")
        if key == "reply_to" and not EMAIL_RE.match(value):
            raise InvalidOptions("reply_to must be an email address")
        if key == "notification_type" and value not in PUSH_TYPES:
            raise InvalidOptions(f"notification_type must be one of {', '.join(PUSH_TYPES)}")
        if key == "voice" and value not in VOICES:
            raise InvalidOptions(f"voice must be one of {', '.join(VOICES)}")
        if key == "language" and value not in LANGUAGES:
            raise InvalidOptions(f"language must be one of {', '.join(LANGUAGES)}")
        out[key] = value
    if channel == "voice" and out.get("voice") and out.get("language") and VOICES[out["voice"]] != out["language"]:
        raise InvalidOptions(f"{out['voice']} speaks {VOICES[out['voice']]}, not {out['language']}")
    return out


def sms_body(body: str, options: dict | None) -> str:
    """The SMS as it goes out: with the opt-out line unless switched off
    or already present."""
    if (options or {}).get("append_stop", True) is False or "stop" in (body or "").lower():
        return body
    return f"{body.rstrip()} {STOP_LINE}" if body else STOP_LINE


def personalize(text: str, name: str, attributes: dict | None = None) -> str:
    """Fills {first_name}, {name} and the attribute tokens. A token the
    contact has no value for becomes empty rather than going out raw."""
    attrs = attributes or {}
    first = (name or "").split(" ")[0] or "there"
    values = {"first_name": first, "name": name or first, **{k: str(attrs.get(k, "") or "") for k in TOKENS if k not in ("first_name", "name")}}
    return re.sub(r"\{(" + "|".join(TOKENS) + r")\}", lambda m: values[m.group(1)], text or "")
