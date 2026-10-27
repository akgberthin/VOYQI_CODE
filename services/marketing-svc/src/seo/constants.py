"""Fixed vocabularies of the SEO engine: task types and who owns them,
autonomy defaults, caps, integration kinds, fix policy, the link-scheme
gate and the Business Profile checklist."""

AGENT_ACTOR_PREFIX = "ai:"

AUTOPILOT_MODES = ("off", "assisted", "autopilot")
REPORT_FREQUENCIES = ("off", "daily", "weekly", "monthly")

# task type -> (owning agent, default autonomy, label)
TASK_TYPES: dict[str, tuple[str, str, str]] = {
    "strategy_refresh": ("seo_strategy_agent", "auto", "Refresh the SEO strategy"),
    "keyword_research": ("keyword_research_agent", "auto", "Keyword research"),
    "competitor_research": ("search_intelligence_agent", "auto", "Competitor research"),
    "research_digest": ("search_intelligence_agent", "auto", "Search / community / news / video research digest"),
    "content_create": ("seo_content_agent", "auto", "Create SEO content"),
    "content_refresh": ("seo_content_agent", "auto", "Refresh existing content"),
    "technical_audit": ("technical_seo_agent", "auto", "Technical SEO audit"),
    "technical_fix": ("technical_seo_agent", "auto", "Apply a technical fix"),
    "schema_implementation": ("technical_seo_agent", "auto", "Implement structured data"),
    "redirect_fix": ("technical_seo_agent", "auto", "Fix redirects"),
    "geo_check": ("geo_agent", "auto", "Check AI-answer visibility"),
    "geo_optimize": ("geo_agent", "auto", "Improve AI-answer visibility"),
    "link_prospect": ("link_building_agent", "auto", "Find link prospects"),
    "link_outreach": ("link_building_agent", "auto", "Link outreach"),
    "cro_analysis": ("cro_agent", "auto", "Conversion analysis"),
    "cro_experiment": ("cro_agent", "auto", "Run a conversion experiment"),
    "pseo_batch": ("programmatic_seo_agent", "auto", "Generate programmatic pages"),
    "local_audit": ("local_seo_agent", "auto", "Local SEO audit"),
    "gbp_optimize": ("local_seo_agent", "auto", "Optimize Google Business Profile"),
    "report": ("seo_reporting_agent", "auto", "Progress report"),
    "integration_sync": ("seo_integration_agent", "auto", "Sync connected data sources"),
    "custom": ("seo_strategy_agent", "approval", "Custom request"),
}

# Task types that only read and analyse — they run unattended in
# "assisted" mode. Everything else changes the site or sends messages.
ASSISTED_SAFE = frozenset({
    "strategy_refresh", "keyword_research", "competitor_research", "research_digest", "technical_audit",
    "geo_check", "cro_analysis", "local_audit", "report", "integration_sync", "link_prospect",
})

CAPS = ("max_publish_per_week", "max_pseo_pages_per_day", "max_outreach_per_day", "max_auto_fixes_per_day", "max_concurrent_tasks")
DEFAULT_CAPS = {"max_publish_per_week": 4, "max_pseo_pages_per_day": 25, "max_outreach_per_day": 10,
                "max_auto_fixes_per_day": 20, "max_concurrent_tasks": 5}
CAP_FOR_TASK = {
    "content_create": "max_publish_per_week", "content_refresh": "max_publish_per_week",
    "pseo_batch": "max_pseo_pages_per_day", "link_outreach": "max_outreach_per_day",
    "technical_fix": "max_auto_fixes_per_day", "redirect_fix": "max_auto_fixes_per_day",
    "schema_implementation": "max_auto_fixes_per_day",
}

OPEN_TASK_STATUSES = ("backlog", "ready", "awaiting_approval", "in_progress")

# kind -> (label, what it powers, providers with a working adapter)
INTEGRATION_KINDS: dict[str, tuple[str, list[str], tuple[str, ...]]] = {
    "cms": ("Website / CMS", ["Automated content publishing", "Automated technical fixes", "Programmatic landing pages",
                              "Schema implementation", "Redirect remediation"], ("wordpress", "webhook", "sample")),
    "search_console": ("Google Search Console", ["Keyword-ranking metrics", "Organic traffic and click metrics",
                                                 "Content refresh detection", "Indexing issue detection"], ("google", "sample")),
    "analytics": ("Web analytics", ["Organic traffic and click metrics", "Conversion rate optimization measurement",
                                    "Recurring progress reports"], ("ga4", "sample")),
    "website_builder": ("Website builder", ["Website-builder connection", "Automated content publishing"], ("webhook",)),
    "business_profile": ("Google Business Profile", ["Google Business Profile optimization", "Local SEO and map-pack optimization"],
                         ("google-business-profile", "manual")),
    "serp_data": ("Search-results data", ["Search-results research", "Keyword-ranking metrics", "Local pack rank checks"],
                  ("dataforseo", "sample")),
    "keyword_data": ("Keyword data", ["Keyword research and search-intent analysis", "Competitor research"], ("dataforseo", "sample")),
    "backlink_index": ("Backlink index", ["Backlink acquisition", "Domain authority monitoring"], ("dataforseo", "sample")),
    "geo_engine": ("AI answer engines", ["Generative Engine Optimization", "AI-answer citation visibility"], ("llm-apis",)),
    "community_research": ("Community research (forums)", ["Reddit, news, and YouTube research"], ("reddit",)),
    "news_research": ("News research", ["Reddit, news, and YouTube research"], ("google-news",)),
    "video_research": ("Video research", ["Reddit, news, and YouTube research"], ("youtube",)),
    "email_inbox": ("Task-request inbox", ["Custom email task requests"], ("inbound-webhook",)),
    "crawler": ("Site crawler", ["Automated technical SEO audits", "Site speed optimization", "Crawl and indexing issue remediation"],
                ("builtin",)),
}

# The super admin integration (rbac-svc) whose credentials each provider
# uses. Secrets live there, encrypted, never in an SEO integration row.
PROVIDER_CREDENTIALS = {
    "wordpress": "wordpress", "webhook": "cms_webhook", "google": "google_seo", "ga4": "google_seo",
    "google-business-profile": "google_business_profile", "dataforseo": "dataforseo", "youtube": "youtube",
}

FIX_POLICY = {
    "auto_apply": ["add_alt_text", "add_redirect", "add_schema", "add_to_sitemap", "fix_redirect_chain",
                   "make_redirect_permanent", "update_meta_description", "update_title"],
    "never_auto": ["block_indexing", "change_url_structure", "delete_page", "remove_noindex", "set_canonical", "update_robots"],
    "core_web_vitals": {"lcp_ms": [2500, 4000], "cls": [0.1, 0.25], "inp_ms": [200, 500]},
}

BLOCKED_LINK_TYPES = ["paid", "pbn", "comment_spam", "link_farm", "directory_spam", "hacked", "exchange"]
ALLOWED_LINK_TYPES = ["editorial", "guest_post", "resource_page", "broken_link", "digital_pr", "partnership", "sponsored"]
SPAM_SCORE_BLOCK = 60
MIN_LINK_RELEVANCE = 30
PROSPECT_TRANSITIONS = {
    "prospect": ["approved_for_outreach", "rejected"],
    "approved_for_outreach": ["contacted", "rejected"],
    "contacted": ["replied", "rejected", "lost"],
    "replied": ["acquired", "rejected", "lost"],
    "acquired": ["lost"],
    "rejected": [], "lost": [], "blocked": [],
}

BANNED_PHRASES = sorted(["guaranteed", "risk-free", "no risk", "cure", "free money", "best in the world", "unbeatable",
                         "100% satisfaction", "can't lose", "#1 rated", "number one"])

CONTENT_KINDS = ("blog", "landing", "refresh", "faq", "pillar")
TARGET_WORDS = {"blog": 800, "landing": 400, "faq": 500, "pillar": 1500, "refresh": 800}
# A draft scoring below this goes back for revision instead of publishing.
MIN_SEO_SCORE = 70

GBP_CHECKLIST = [
    {"item": "name", "weight": 6, "how": "Use your real business name, exactly as on signage.", "agent_can_do": False},
    {"item": "primary_category", "weight": 8, "how": "Choose the most specific primary category.", "agent_can_do": False},
    {"item": "additional_categories", "weight": 4, "how": "Add up to a few relevant secondary categories.", "agent_can_do": False},
    {"item": "address", "weight": 8, "how": "Add the full street address (or service area).", "agent_can_do": False},
    {"item": "phone", "weight": 6, "how": "Add a local phone number that matches your website.", "agent_can_do": False},
    {"item": "website", "weight": 6, "how": "Link the location's page on your website.", "agent_can_do": False},
    {"item": "hours", "weight": 8, "how": "Set regular opening hours.", "agent_can_do": False},
    {"item": "special_hours", "weight": 2, "how": "Set holiday and special hours.", "agent_can_do": False},
    {"item": "description", "weight": 8, "how": "Write a description of at least 250 characters that says what you offer and where.", "agent_can_do": True},
    {"item": "services", "weight": 9, "how": "List every service you offer.", "agent_can_do": True},
    {"item": "photos", "weight": 10, "how": "Upload at least 10 real photos (exterior, interior, team, product).", "agent_can_do": False},
    {"item": "logo", "weight": 3, "how": "Upload your logo.", "agent_can_do": False},
    {"item": "cover", "weight": 3, "how": "Upload a cover photo.", "agent_can_do": False},
    {"item": "posts", "weight": 6, "how": "Publish at least one update post per week.", "agent_can_do": True},
    {"item": "qa", "weight": 3, "how": "Answer the questions people have asked.", "agent_can_do": True},
    {"item": "reviews", "weight": 6, "how": "Have at least 10 reviews averaging 4.0 or higher (earned, never bought).", "agent_can_do": False},
    {"item": "review_response", "weight": 4, "how": "Reply to at least 80% of reviews.", "agent_can_do": True},
]

GEO_ENGINES = ("chatgpt", "perplexity", "claude", "google_ai")

RESEARCH_SOURCES = [
    ("serp", "Search-results research", "serp_data"),
    ("competitor", "Competitor analysis", "keyword_data"),
    ("reddit", "Reddit research", "community_research"),
    ("news", "News research", "news_research"),
    ("youtube", "YouTube research", "video_research"),
    ("multi_engine", "Multi-search-engine research", "serp_data"),
]

IMPACT_DISCLAIMER = ("Impact is a relative 0-100 ordering score derived from the evidence shown, "
                     "not a traffic or revenue forecast.")
GEO_DISCLAIMER = ("Recommendations raise the likelihood of being mentioned and cited in AI answers; "
                  "no one can guarantee what an AI engine says.")
AUTHORITY_NOTE = ("Authority is a provider-defined score (for example Domain Rating or Domain Authority), "
                  "comparable only within the same provider.")
