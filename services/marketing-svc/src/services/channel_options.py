"""Per-channel message settings stored on a draft (`options`) and handed
to notification-svc with every delivery, plus the personalisation tokens
a message may use.

email  preview_text, from_name, reply_to
sms    append_stop (default true: "Reply STOP to opt out." is added unless
       the message already says STOP — TCPA/CTIA)
push   deep_link, notification_type
voice  voice, language
"""
import re

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PUSH_TYPES = ("promotional", "transactional", "ai_proactive")
# Amazon Polly voices Twilio <Say> supports, by the language they speak.
VOICES = {
    "Polly.Joanna": "en-US", "Polly.Matthew": "en-US", "Polly.Amy": "en-GB", "Polly.Brian": "en-GB",
    "Polly.Lupe": "es-US", "Polly.Miguel": "es-US", "Polly.Lea": "fr-FR", "Polly.Mathieu": "fr-FR", "Polly.Zeina": "arb",
}
LANGUAGES = ("en-US", "en-GB", "es-US", "fr-FR", "arb")
STOP_LINE = "Reply STOP to opt out."

# {token}: the contact field or attribute that fills it.
TOKENS = {"first_name": "first name", "name": "name", "city": "city", "class": "mobility class", "hub": "home hub",
          "vehicle": "vehicle", "gu_balance": "Growth Units balance"}


class InvalidOptions(ValueError):
    pass


def validate(channel: str, options: dict | None) -> dict:
    """The options this channel accepts, cleaned — or InvalidOptions."""
    o = dict(options or {})
    allowed = {"email": {"preview_text", "from_name", "reply_to"}, "sms": {"append_stop"}, "push": {"deep_link", "notification_type"},
               "voice": {"voice", "language"}}.get(channel, set())
    unknown = set(o) - allowed
    if unknown:
        raise InvalidOptions(f"{channel} messages don't take {', '.join(sorted(unknown))}")
    out: dict = {}
    for key, value in o.items():
        if value in (None, ""):
            continue
        if key == "append_stop":
            if not isinstance(value, bool):
                raise InvalidOptions("append_stop must be true or false")
            out[key] = value
            continue
        if not isinstance(value, str):
            raise InvalidOptions(f"{key} must be text")
        value = " ".join(value.split())
        limits = {"preview_text": 200, "from_name": 80, "reply_to": 320, "deep_link": 512}
        if key in limits and len(value) > limits[key]:
            raise InvalidOptions(f"{key} is longer than {limits[key]} characters")
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
