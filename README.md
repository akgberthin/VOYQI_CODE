# VOYQI Mobility OS

Multi-tenant mobility platform: 25 FastAPI microservices behind one API
gateway, an AI orchestration nucleus (Anthropic Claude, with a
deterministic fallback when no API key is set), Kafka event streaming,
and a database per service.

## Layout

| Path | Contents |
|---|---|
| `services/` | Backend microservices (FastAPI + async SQLAlchemy + Alembic + pytest) |
| `docker-compose.yml` | Local environment: every service, one Postgres per service, Kafka |
| `infrastructure/` | Terraform (GCP: GKE, Cloud SQL, Redis, Kafka, IAM) and Kubernetes/Kustomize manifests |
| `scripts/` | Demo seed, guided demo walkthrough, role smoke test, telematics simulator, manifest generator |
| `docs/` | Architecture, service inventory, port registry, autonomy tiering, AI scaling |
| `docs/implementation-guides/` | Per-module implementation and installation guides |
| `frontend/` | The console: one Next.js app with the super admin (`/admin`) and marketing (`/marketing`) consoles |
| `prototypes/` | Single-file HTML UI prototypes (design reference, not the production frontend) |

## Run locally

```bash
cp .env.example .env              # optionally set ANTHROPIC_API_KEY for live Claude reasoning
docker compose up -d --build      # every service waits for its DB and Kafka, then migrates and serves
python3 scripts/seed_demo.py      # demo tenant, one login per role, sample data
python3 scripts/role_smoke_test.py
```

Gateway: <http://localhost:8000>. Every service also listens on its own
port for local debugging; clients should only ever use the gateway.

Simulated vehicle telematics (no real devices yet). The simulator
publishes over MQTT like a real unit (`voyqi/<tenant>/vehicles/<VIN>/telemetry|tamper`)
to the Mosquitto broker on port 1883; fleet-svc subscribes, stores each
reading, geofences it against the vehicle's hub, raises an alert for the
Fleet Intelligence Agent when a vehicle leaves the hub area, and sends
tamper signals to human security review:

```bash
docker compose --profile simulator up telematics-simulator
# or: python3 scripts/telematics_simulator.py --transport mqtt --mqtt-host localhost --tenant voyqi-demo --vehicles 12 --ticks 30
# or over HTTP ingest: python3 scripts/telematics_simulator.py --tenant voyqi-demo --vehicles 12 --ticks 30
```

In deployed environments devices use mutual TLS with the VIN as the
certificate Common Name, and the broker only lets a device publish under
its own VIN (`infrastructure/mqtt/`, `docs/DEPLOYMENT.md`).

## Console (Next.js)

`frontend/` is one Next.js (App Router, TypeScript) app holding both
consoles behind a single sign-in. After login it checks with the gateway
which consoles the account may use and shows only those; an account with
both gets a switcher in the top bar.

```bash
docker compose up -d                    # http://localhost:3001
# or, while developing:
cd frontend && npm install
VOYQI_GATEWAY_URL=http://localhost:8000 npm run dev    # http://localhost:3000
```

The browser only talks to the console's own origin: `/api/*` is
forwarded to the gateway by `src/app/api/[...path]/route.ts` (target from
`VOYQI_GATEWAY_URL`, read at request time), so the HttpOnly session
cookie stays same-origin. Layout: `src/app/` routes, `src/features/admin`
and `src/features/marketing` pages, `src/lib` (API client, session, UI
kit), `src/components` (shell, login).

## Super admin console

`/admin` — the operator's control panel for the whole platform, for a
user with the platform permission (`super.admin@voyqi.example` in the
demo):

- **Services & APIs** — health of every service, and every API each one
  exposes, callable from forms generated from the service's OpenAPI
  description (via `GET /api/v1/platform/services[/{name}/openapi]` on
  the gateway).
- **AI** — every agent with a per-tenant kill switch, the approval
  queue, autonomy tiers, prompt versions (evaluate → promote), goals and
  the decision log.
- **Access & security** — users, roles, overrides, ABAC policies,
  tenants, break-glass/impersonation sessions and the audit log.

## Marketing console

`/marketing` — the Marketing Command Center, for a user with marketing
permissions (`marketing.manager@voyqi.example`):

- **Campaigns & composers** — email, SMS, push and voice with live
  preview; content through the brand/compliance guardrail; sends go to
  the campaign's segment, personalised (`{first_name}`), suppression-
  checked, and run automatically at their time (recurring if set).
- **Real delivery** through the providers configured in super admin →
  Integrations (SMTP, Twilio SMS/voice, FCM). Without a provider a
  message is only logged ("console only") and never counted as reach.
- **Social** — Facebook, Instagram, LinkedIn and X publishing through the
  Meta/LinkedIn/X integrations; hosted images (uploaded or generated with
  OpenAI); **autopilot** plans the week's posts (Suggest or Auto-publish;
  guardrail-flagged copy always waits for a person).
- **AI studio** — copy variants per tone for a class, channel and
  objective from the Marketing Content Agent.
- **Automation flows** — event-triggered journeys (sign-up, lead,
  cancellation, new CRM contact) or segment enrolment, multi-step across
  channels.
- **Voice compliance** — calls reach only contacts with voice consent,
  open by saying they're automated, and wait for the calling window
  (set per tenant, within 08:00-21:00).
- **Composers per channel** — email sender name, reply-to, preview
  text and an A/B subject; SMS part counting and the opt-out line (added
  unless the message says STOP); push title and deep link; AI voice and
  language. Tokens (`{first_name}`, `{city}`, `{class}`…) are filled from
  each contact's attributes.
- **Audience builder** — contacts imported as CSV keep any extra columns
  as attributes; rules over them give a live estimate per channel and can
  be saved as a segment filled with the matching contacts.
- Suppression list, A/B tests, calendar, analytics (date range, funnel),
  reports (CSV, or emailed weekly/monthly to recipients), leads.

Dev inbox: emails sent by the platform appear in Mailpit at
http://localhost:8125 once the SMTP integration points at host `mailpit`,
port 1025, STARTTLS false.

## Growth / SEO engine and agents

The Growth module of the marketing command center: an SEO engine in
marketing-svc (`services/marketing-svc/src/seo`, API under
`/api/v1/marketing/seo`) and fourteen agents in ai-orchestration-svc
(`services/ai-orchestration-svc/src/services/seo_agent.py`):

| Agent | Does |
|---|---|
| SEO Strategy | strategy from real data, ordered by impact per effort, with its tasks |
| Keyword Research | site and competitor keywords, gaps, questions, buying intent |
| Search Intelligence | Google/Bing results, competitors, Reddit, news, YouTube |
| SEO Content | briefs, articles and refreshes, scored and guardrail-checked, scheduled |
| Technical SEO | crawl audits, Core Web Vitals, safe fixes (titles, descriptions, alt text, schema, redirects) |
| GEO | brand mentions and citations in ChatGPT, Perplexity and Claude answers |
| Link Building | prospects from competitors' links, compliant outreach, authority |
| CRO | conversion reviews of key pages, A/B experiments (z-test) |
| Programmatic SEO | template pages behind the thin-content / uniqueness gate |
| Local SEO | Business Profile completeness, NAP consistency, local-pack ranks |
| Reporting | daily / weekly / monthly reports, emailed to the site's recipients |
| Integration | keeps Search Console, GA4, backlinks and profiles synced |
| Autopilot | every 30 min per site: replans and runs what the plan allows |
| Task Intake | turns emailed requests from authorised senders into tasks |

**How autonomy works.** Each site has a mode — *off*, *assisted*
(analysis runs unattended; anything that changes the site or sends
messages waits for a person) or *autopilot* — plus per-task-type
overrides and caps (publishes per week, programmatic pages and outreach
per day, auto-fixes per day, tasks at once). Hard gates hold whatever the
mode: only a person clears guardrail-held content or held programmatic
pages, approves fixes outside the low-risk list, starts sponsored links
or changes a location's name, address, phone or categories; link schemes
are never pursued; outreach needs opt-out language and respects the
suppression list; emailed requests that read like instructions to an AI
wait for approval. Without an Anthropic key, agents use templates — and
template-written content is held for a person, and live pages are never
rewritten.

**Connections** (per site, under the Growth module's setup; credentials
in super admin → Integrations, category SEO): WordPress or a signed
website webhook (publishing and fixes), Google Search Console + GA4
(service account), DataForSEO (keywords, search results, local packs,
backlinks), the built-in crawler with PageSpeed, OpenAI / Perplexity /
Anthropic for AI-answer checks, Google Business Profile, Reddit, Google
News, YouTube, and an inbound-mail webhook for requests
(`/api/v1/marketing/public/seo/inbound/{site_id}?token=…`). For local
demos, `MARKETING_SEO_SAMPLE_ADAPTERS=true` (the compose default) adds a
labelled *sample* provider that serves demo data; keep it off anywhere
real numbers are expected.

## Demo console

A browser console for showing the platform before the React apps exist —
roles & access, lead → loan decision, customer inbox, live fleet map,
AI governance and emergency access, all live through the gateway:

```bash
python3 scripts/seed_demo.py
python3 demo-console/server.py     # http://localhost:8090
```

## Demo logins

`scripts/seed_demo.py` creates these in tenant `voyqi-demo` (send
`X-Tenant-Id: voyqi-demo` on login), all with password `Voyqi-Demo-2026!`.
The sample business data is invented — replace it before an external demo.

| Role | Login | Can reach |
|---|---|---|
| Super Admin | `super.admin@voyqi.example` | everything |
| Fleet Manager | `fleet.manager@voyqi.example` | fleet, hubs, rental (read), AI |
| Dealer GM | `dealer.gm@voyqi.example` | DMS, sales, originations, CRM, pricing (read), AI |
| HR Manager | `hr.manager@voyqi.example` | HRM, users (read), notifications, AI |
| Marketing Manager | `marketing.manager@voyqi.example` | marketing, CRM, AI |
| Executive | `executive@voyqi.example` | executive, management, real estate, capital finance, insurance risk — read only |
| Compliance Officer | `compliance.officer@voyqi.example` | security & compliance, identity sessions, RBAC (read), insurance risk, AI |
| Member | `member@voyqi.example`, `member.two@voyqi.example` | their own membership, subscriptions and payments; hubs and prices (read) |

## How requests are authorized

The gateway is the only ingress. Login sets an HttpOnly JWT cookie; on
every other request the gateway verifies it, takes tenant and user from
the token (never from client headers), and checks the user's role
permissions from rbac-svc. Each route prefix is a module — reading needs
`<module>.read`, writing needs `<module>.write`; `<module>.*` and `*` are
wildcards; a deny override always wins. `<module>.self` (members) allows
only the self-service endpoints in
`services/api-gateway-svc/src/services/self_service.py`, and only on the
caller's own records — ownership is checked with the owning service
before forwarding. If rbac-svc is unreachable the
request is refused. Self-service paths (`/api/v1/auth/*`, the caller's
own user record) need no role. `GATEWAY_ENFORCE_RBAC=false` turns
enforcement off for debugging.

Logout, password reset and refresh-token reuse revoke access tokens
immediately through Redis; without Redis a token stays valid until it
expires (15 minutes).

## How the AI acts

Services publish every state change to Kafka in one envelope
(`event_type`, `tenant_id`, `occurred_at`, `payload`). The AI nucleus
consumes all platform topics and routes each event to its agent.
Whether an agent's action runs immediately or waits for a human is set
per action in `services/ai-orchestration-svc/src/core/tier_policy.yaml`
(mirrored in the Kubernetes ConfigMap):

- **Tier 1** — runs immediately, logged.
- **Tier 2** — queued as a recommendation; runs only when approved (`POST /api/v1/ai/recommendations/{id}/approve`).
- **Tier 3** — like tier 2, and approval requires a named `accountable_role`. Credit decisions, ABAC policy grants, hiring/discipline decisions, liability assessment and tamper events.

`GET /api/v1/ai/audit/tier-graduation` reports approval and override
rates per action — the evidence for promoting a tier-2 action to tier 1.

Per-tenant tier changes go through `/api/v1/ai/tier-policy`
(`GET` effective policy, `PUT|DELETE /{policy_agent}/{action_type}`,
`GET .../evidence`). Tier-3 actions can't be changed, nothing can be
raised to tier 3 there, and removing an approval gate (→ tier 1) needs
≥20 decisions in 90 days with ≥90% approved.

Agent system prompts are versioned per prompt key (`GET /api/v1/ai/prompts`,
`POST /api/v1/ai/policies` with `prompt_key`, then `/evaluate` and
`/promote`). A version can only be promoted after a stored evaluation;
the promoted version is used for that tenant's agent calls, and replicas
pick up changes within a minute.

## Local ports

| Port | Service | Port | Service |
|---|---|---|---|
| 8000 | api-gateway-svc | 8023 | hrm-svc |
| 8003 | subscription-svc | 8024 | auth-svc |
| 8004 | fleet-svc | 8025 | user-svc |
| 8005 | rental-svc | 8026 | payment-svc |
| 8006 | hub-svc | 8027 | notification-svc |
| 8007 | marketing-svc | 8028 | ip-asset-svc |
| 8008 | dms-svc | 8029 | insurance-risk-svc |
| 8009 | sales-service | 8031 | security-compliance-svc (also `/api/v1/identity`) |
| 8010 | pricing-svc | 8032 | executive-svc |
| 8011 | rbac-svc (also page content, settings, themes) | 8033 | management-svc |
| 8020 | ai-orchestration-svc | 8034 | real-estate-svc |
| 8021 | crm-svc | 8035 | capital-finance-svc |
| 9092 | Kafka | 8036 | originations-svc (also `/api/v1/org`) |
| 6379 | Redis | 1883 | MQTT broker (Mosquitto) |
| 3001 | Console (`/admin`, `/marketing`) | | |

The Kubernetes service catalog uses the same names and ports.

## Database schema

Each service's schema is owned by its Alembic migrations; containers run
`alembic upgrade head` before serving (`create_all` is used only for
SQLite in tests). To change a model: edit it, then
`alembic revision --autogenerate -m "..."` in that service.

## Tests

```bash
cd services/<service-name>
pip install -r requirements.txt
pytest
```

Start with `docs/ARCHITECTURE_AND_INFRASTRUCTURE.md`.
