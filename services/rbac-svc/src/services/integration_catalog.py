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
            {"key": "host", "label": "Host", "required": True, "hint": "smtp.sendgrid.net (local dev inbox: mailpit, port 1025, STARTTLS false)"},
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
    "google_seo": {
        "label": "Google Search Console & Analytics", "category": "SEO",
        "used_for": "SEO agents: Search Console clicks, rankings and pages; GA4 traffic and conversions; PageSpeed Core Web Vitals.",
        "fields": [
            {"key": "service_account_json", "label": "Service account JSON", "secret": True, "required": True, "multiline": True,
             "hint": "Add the service account's email as a user on the Search Console property and the GA4 property"},
            {"key": "pagespeed_api_key", "label": "PageSpeed Insights API key", "secret": True, "hint": "Optional: raises the PageSpeed quota"},
        ],
    },
    "dataforseo": {
        "label": "DataForSEO", "category": "SEO",
        "used_for": "SEO agents: keyword data, competitor gaps, search results, local-pack ranks and the backlink index.",
        "fields": [
            {"key": "login", "label": "API login", "required": True},
            {"key": "password", "label": "API password", "secret": True, "required": True},
        ],
    },
    "wordpress": {
        "label": "WordPress", "category": "SEO",
        "used_for": "SEO agents publish articles and landing pages and apply safe fixes (titles, descriptions, alt text, redirects).",
        "fields": [
            {"key": "base_url", "label": "Site URL", "required": True, "hint": "https://voyqi.com"},
            {"key": "username", "label": "Username", "required": True, "hint": "An Editor or Administrator"},
            {"key": "app_password", "label": "Application password", "secret": True, "required": True,
             "hint": "Users → Profile → Application Passwords. Redirects need the Redirection plugin."},
        ],
    },
    "cms_webhook": {
        "label": "Website webhook", "category": "SEO",
        "used_for": "For any other CMS or website builder: VOYQI posts signed publish and fix requests your site applies.",
        "fields": [
            {"key": "url", "label": "Webhook URL", "required": True},
            {"key": "signing_secret", "label": "Signing secret", "secret": True, "required": True,
             "hint": "Requests carry X-VOYQI-Signature: sha256=HMAC(body)"},
        ],
    },
    "google_business_profile": {
        "label": "Google Business Profile", "category": "SEO",
        "used_for": "Local SEO agent: reads profiles and publishes descriptions and update posts.",
        "fields": [
            {"key": "client_id", "label": "OAuth client ID", "required": True},
            {"key": "client_secret", "label": "OAuth client secret", "secret": True, "required": True},
            {"key": "refresh_token", "label": "Refresh token", "secret": True, "required": True,
             "hint": "From an OAuth consent with the business.manage scope"},
            {"key": "account_id", "label": "Account ID", "hint": "accounts/123… (needed for update posts)"},
        ],
    },
    "perplexity": {
        "label": "Perplexity", "category": "AI",
        "used_for": "GEO agent: checks how the brand appears in Perplexity answers.",
        "fields": [{"key": "api_key", "label": "API key", "secret": True, "required": True}],
    },
    "youtube": {
        "label": "YouTube Data API", "category": "SEO",
        "used_for": "Search intelligence agent: video research.",
        "fields": [{"key": "api_key", "label": "API key", "secret": True, "required": True}],
    },
    "reddit": {
        "label": "Reddit", "category": "SEO",
        "used_for": "Search intelligence agent: community research (optional — public search works without it, at lower reliability).",
        "fields": [
            {"key": "client_id", "label": "App client ID", "required": True},
            {"key": "client_secret", "label": "App secret", "secret": True, "required": True},
        ],
    },
    "seo_inbox": {
        "label": "SEO task inbox", "category": "SEO",
        "used_for": "Task intake agent: emailed requests forwarded by your mail provider's inbound webhook.",
        "fields": [{"key": "token", "label": "Webhook token", "secret": True, "required": True,
                    "hint": "Any long random string; put it in the inbound webhook URL as ?token=…"}],
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
