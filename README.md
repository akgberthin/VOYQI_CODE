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
| `scripts/` | Demo seed, role smoke test, telematics simulator, manifest generator |
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

Simulated vehicle telematics (no real devices yet):

```bash
docker compose --profile simulator up telematics-simulator
# or: python3 scripts/telematics_simulator.py --tenant voyqi-demo --vehicles 12 --ticks 30
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
| Member | `member@voyqi.example` | subscriptions, rentals, hubs (read), payments, pricing (read) |

## How requests are authorized

The gateway is the only ingress. Login sets an HttpOnly JWT cookie; on
every other request the gateway verifies it, takes tenant and user from
the token (never from client headers), and checks the user's role
permissions from rbac-svc. Each route prefix is a module — reading needs
`<module>.read`, writing needs `<module>.write`; `<module>.*` and `*` are
wildcards; a deny override always wins. If rbac-svc is unreachable the
request is refused. Self-service paths (`/api/v1/auth/*`, the caller's
own user record) need no role. `GATEWAY_ENFORCE_RBAC=false` turns
enforcement off for debugging.

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

These differ from `docs/PORT_REGISTRY.md` and the Kubernetes service
catalog, which use different names and ports for some services — align
them before the first cluster deployment.

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
