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

## Super admin console

`frontend/super-admin` (React + TypeScript) — the operator's control
panel for the whole platform, signed in through the gateway as a user
with the platform permission (`super.admin@voyqi.example` in the demo):

- **Services & APIs** — health of every service, and every API each one
  exposes, callable from forms generated from the service's OpenAPI
  description (via `GET /api/v1/platform/services[/{name}/openapi]` on
  the gateway).
- **AI** — every agent with a per-tenant kill switch, the approval
  queue, autonomy tiers, prompt versions (evaluate → promote), goals and
  the decision log.
- **Access & security** — users, roles, overrides, ABAC policies,
  tenants, break-glass/impersonation sessions and the audit log.

```bash
docker compose up -d                    # serves it at http://localhost:3001
# or, while developing: cd frontend && npm install && npm run dev -w super-admin   # :5180
```

## Marketing console

`frontend/marketing` — the Marketing Command Center (React prototype
port), signed in as a user with marketing permissions
(`marketing.manager@voyqi.example`): campaigns with their content, sends
and results; per-channel composers (email, SMS, push, voice) with live
preview; AI drafting through the Marketing Content Agent; the guardrail
review queue; social accounts and posts; audience segments and the
suppression list; A/B tests with significance; calendar; analytics;
library; leads.

```bash
docker compose up -d                    # http://localhost:3002
# or: cd frontend && npm install && npm run dev -w marketing    # :5181
```

The consoles share `frontend/shared/` (API client, session, layout, UI
kit) through npm workspaces.

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
| 3001 | Super admin console | 3002 | Marketing console |

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
