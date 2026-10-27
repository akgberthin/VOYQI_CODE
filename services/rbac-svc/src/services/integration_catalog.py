"""Every third-party integration the platform can use, with the fields
each needs. `secret` fields are never returned by the API (only whether
they're set); everything else is shown as entered."""

CATALOG: dict[str, dict] = {
    "anthropic": {
        "label": "Anthropic Claude", "category": "AI",
        "used_for": "Every AI agent's reasoning (otherwise agents run their rule-based fallback).",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True, "hint": "sk-ant-api03-… (a workspace API key with credits)"},
            {"key": "model", "label": "Model", "default": "claude-sonnet-5-5"},
            {"key": "workspace_id", "label": "Workspace ID", "hint": "Only for keys not scoped to a workspace"},
        ],
    },
    "openai": {
        "label": "OpenAI", "category": "AI",
        "used_for": "Marketing image generation (AI studio, social creatives).",
        "fields": [
            {"key": "api_key", "label": "API key", "secret": True, "required": True},
            {"key": "image_model", "label": "Image model", "default": "gpt-image-1"},
        ],
    },
    "smtp": {
        "label": "Email (SMTP)", "category": "Messaging",
        "used_for": "Marketing and system email.",
        "fields": [
            {"key": "host", "label": "Host", "required": True, "hint": "smtp.sendgrid.net"},
            {"key": "port", "label": "Port", "default": "587"},
            {"key": "username", "label": "Username"},
            {"key": "password", "label": "Password", "secret": True},
            {"key": "from_address", "label": "From address", "required": True, "hint": "hello@voyqi.com"},
            {"key": "use_tls", "label": "STARTTLS", "default": "true", "choices": ["true", "false"]},
        ],
    },
    "twilio": {
        "label": "Twilio (SMS & voice)", "category": "Messaging",
        "used_for": "SMS campaigns and AI voice calls.",
        "fields": [
            {"key": "account_sid", "label": "Account SID", "required": True, "hint": "AC…"},
            {"key": "auth_token", "label": "Auth token", "secret": True, "required": True},
            {"key": "from_number", "label": "From number", "required": True, "hint": "+15125550100"},
            {"key": "voice", "label": "Voice", "default": "Polly.Joanna", "hint": "Twilio <Say> voice"},
        ],
    },
    "fcm": {
        "label": "Firebase Cloud Messaging", "category": "Messaging",
        "used_for": "Mobile push notifications (Android and iOS through APNs in Firebase).",
        "fields": [
            {"key": "service_account_json", "label": "Service account JSON", "secret": True, "required": True, "multiline": True},
        ],
    },
    "meta": {
        "label": "Meta (Facebook & Instagram)", "category": "Social",
        "used_for": "Publishing posts to a Facebook Page and its Instagram business account.",
        "fields": [
            {"key": "page_id", "label": "Facebook Page ID", "required": True},
            {"key": "page_access_token", "label": "Page access token", "secret": True, "required": True},
            {"key": "instagram_account_id", "label": "Instagram business account ID"},
        ],
    },
    "linkedin": {
        "label": "LinkedIn", "category": "Social",
        "used_for": "Publishing posts as a LinkedIn organization.",
        "fields": [
            {"key": "organization_urn", "label": "Organization URN", "required": True, "hint": "urn:li:organization:123456"},
            {"key": "access_token", "label": "Access token", "secret": True, "required": True},
        ],
    },
    "x": {
        "label": "X (Twitter)", "category": "Social",
        "used_for": "Publishing posts on X.",
        "fields": [
            {"key": "access_token", "label": "OAuth 2.0 user access token", "secret": True, "required": True},
        ],
    },
    "stripe": {
        "label": "Stripe", "category": "Payments",
        "used_for": "Subscription and rental payments.",
        "fields": [
            {"key": "api_key", "label": "Secret key", "secret": True, "required": True, "hint": "sk_live_… / sk_test_…"},
            {"key": "webhook_secret", "label": "Webhook signing secret", "secret": True},
        ],
    },
}


def secret_keys(provider: str) -> set[str]:
    return {f["key"] for f in CATALOG[provider]["fields"] if f.get("secret")}


def missing_required(provider: str, config: dict) -> list[str]:
    return [f["key"] for f in CATALOG[provider]["fields"] if f.get("required") and not str(config.get(f["key"], "")).strip()]


def with_defaults(provider: str, config: dict) -> dict:
    out = {f["key"]: f["default"] for f in CATALOG[provider]["fields"] if "default" in f}
    out.update({k: v for k, v in config.items() if v not in (None, "")})
    return out
