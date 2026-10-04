# VOYQI Mobility OS — Architecture & Infrastructure

*"Your Mobility Travels With You™"*

**This document is the authoritative, current architecture & infrastructure
reference for VOYQI Mobility OS.** It reconciles and supersedes architecture
notes scattered across the PRD, Engineer Handoff, Software Development
Agreement, and prior build sessions — several of which describe an earlier
or partially-superseded state (13 services on AWS EKS; 14 services; etc.).
The state below is current as of this build.

If anything elsewhere conflicts with this document on **cloud provider,
service count, or port assignments**, this document is correct.

---

## 1. What changed in this update

- **Cloud provider corrected: GCP, not AWS.** Earlier infrastructure drafts
  targeted AWS EKS + RDS. The platform runs on **GKE + Cloud SQL**. All
  Terraform in this repository targets GCP; no AWS provider blocks exist
  here or should be reintroduced.
- **Service count corrected: 26, not 13 or 14.** The original PRD's
  13-execution-services-plus-AI-orchestration model covers Layers 0–1. The
  Enterprise Admin Screen Specification (122 admin screens across 17
  primary panels) requires a second layer of 12 admin-panel-backing
  services, bringing the total to 26. See `docs/SERVICE_INVENTORY.md`.
- **Build status reflects reality, not aspiration.** Fleet Management
  (fleet-service + its 13-screen admin UI) is complete and tested. HRM is
  in active development. The 11 other Layer 2 services are scoped but not
  yet built — marked `PLANNED`, not `READY`. See the status column in
  every table in this document.
- **REV D — AI orchestration split into realtime/batch tiers.** The
  nucleus is still one logical service (same shared decision log, same
  Redis checkpoint store — PRD §23.1's "do not build the AI as multiple
  separate services" is not violated), but is now scheduled as two
  Kubernetes deployment groups on two dedicated node pools, fixing three
  compromises: node-level resource contention between latency-critical
  and batch-reasoning agents, shared-blast-radius deploys, and in-memory
  session/checkpoint affinity. Full rationale in
  `docs/AI_ORCHESTRATION_SCALING.md`.

---

## 2. Architecture Principles (enforced, non-negotiable)

1. **AI is the system layer.** The AI orchestration nucleus decides; every
   other service persists, validates, and executes. Neither does the
   other's job. This was Garth's explicit rejection of "AI deferred to a
   later phase" architectures — it's built in from Layer 1, not bolted on.
2. **"AI proposes, human approves"** is the core UX rule for every
   AI-assisted flow (fleet recommendations, email drafting, course prep,
   pricing).
3. **Two-layer agent discipline.** Every AI judgment has a deterministic
   fallback that mirrors backend logic exactly (same thresholds, keyword
   lists, tie-break rules). The platform is never dependent on a model
   call for a critical path.
4. **Structural separation of AI-judged vs. human-only fields.** Liability
   fields, collateral status, and tamper events stay entirely outside the
   AI path — this is enforced in the data model, not just the UI.
5. **Vehicles are software-defined assets.** Fleet state lives in
   `fleet-service` and is the single source of truth every other service
   reads — never duplicates.
6. **API-first, gateway-only ingress.** No service is ever reachable
   directly by a client. All traffic enters through `api-gateway` (port
   8000), enforced by Kubernetes `NetworkPolicy`, not just convention.
7. **Database-per-service.** No shared databases, ever. Each microservice
   owns its own Postgres database + least-privilege user
   (`terraform/modules/cloudsql`).
8. **Multi-tenant by construction.** Every request carries `X-Tenant-Id`.
   Every table in every service is tenant-scoped. Tenants map 1:1 to legal
   entities (VOYQI Holdings, state holding companies, dealership rooftops,
   Fleet, Mobility, Rental, and future white-label operators).
9. **JWT via HttpOnly cookies only.** Never localStorage, never bearer
   tokens in JS-accessible storage.
10. **Every state change is an event.** All services publish to Kafka on
    create/update/delete; the AI nucleus consumes all of it.
11. **Precision and structural correctness over broad protections.**
    Governance (RBAC/ABAC, Class B control) is modeled at the charter
    level, not contractually bolted on — mirrors the dual-class equity
    structure this platform's own tenancy model reflects.

---

## 3. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│   TypeScript / React / TanStack Start / Tailwind      Flutter      │
│        Member App · Operator Portal · Executive Console            │
└─────────────────────────────┬──────────────────────────────────────┘
                              │ HTTPS / JSON
┌─────────────────────────────▼──────────────────────────────────────┐
│                    API GATEWAY — port 8000                         │
│         JWT (HttpOnly cookie) · Rate limiting · Reverse proxy       │
│                    THE ONLY CLIENT-FACING PORT                     │
└───────────────┬───────────────────────────────┬────────────────────┘
                │                               │
        AI decisions                    execution requests
┌───────────────▼───────────────┐   ┌────────────────▼─────────────────┐
│  AI ORCHESTRATION NUCLEUS       │   │   LAYER 1 — CORE EXECUTION (11)   │
│  ONE logical service, 2 tiers,  │   │  user · subscription · fleet      │
│  12 agents (REV D — see         │   │  rental · payment · marketing     │
│  AI_ORCHESTRATION_SCALING.md)   │   │  sales · pricing · rbac           │
│                                  │   │  notification · global-mobility   │
│  REALTIME · 8020 · Guaranteed QoS│  └────────────────┬─────────────────┘
│   Supervisor · Concierge ·      │                     │
│   Member Experience ·           │   ┌────────────────▼─────────────────┐
│   Relationship Intelligence     │   │  LAYER 0 — PLATFORM INFRA (2)     │
│                                  │   │  api-gateway · auth-service       │
│  BATCH · 8014 · Burstable QoS   │   └───────────────────────────────────┘
│   Fleet Intelligence ·          │
│   Pricing Intelligence ·        │   ┌───────────────────────────────────┐
│   Revenue · Retention ·         │   │ LAYER 2 — ADMIN-PANEL-BACKING (12) │
│   Sales & Originations Intel ·  │   │ super-admin · crm · dms            │
│   Talent & Workforce Intel ·    │   │ originations · executive           │
│   Compliance & Risk Intel ·     │   │ management · real-estate           │
│   Portfolio Intelligence        │   │ capital-finance · insurance-risk   │
│                                  │   │ technology-ip · security-compliance│
│  Both tiers share ONE decision  │   │ hrm                                │
│  log + ONE Redis checkpoint     │   └───────────────────────────────────┘
│  store — not 2 independent AI   │
│  services (PRD §23.1)           │
│  Split Kafka consumer groups     │
│  Calls services as tools,       │
│  X-AI-Initiated header          │
└─────────────┬────────────────────┘
              │ Kafka — every service publishes, AI nucleus consumes all
  ┌───────────▼──────────────────────────────────────────────────────┐
  │  Cloud SQL / PostgreSQL (one DB per service, 2 instances)          │
  │  Memorystore / Redis  ·  Kafka (Strimzi on GKE)  ·  Mosquitto MQTT │
  └────────────────────────────────────────────────────────────────────┘

GKE (Google Kubernetes Engine) — private, VPC-native, 3 node pools:
  general-pool (Layers 0/1/2)
  ai-orchestration-realtime-pool (tainted, Guaranteed QoS, PriorityClass 900000)
  ai-orchestration-batch-pool     (tainted, Burstable QoS, PriorityClass 100000)
```

See `docs/AI_ORCHESTRATION_SCALING.md` for the full rationale behind the
realtime/batch split — the short version: it fixes node-level resource
contention, deploy blast radius, Anthropic rate-limit contention, and
session/checkpoint affinity, without splitting the AI into independent
services that don't share context (which PRD §23.1 explicitly rules out).

**26 total services.** Every service owns its own PostgreSQL database — no
shared database, no cross-service SQL join. The AI orchestration service
and the 25 execution/admin services are peers; the API gateway is the only
path from client to backend.

### 3.1 The responsibility split

| Layer | Owns | Does not do |
|---|---|---|
| AI Orchestration | All decisions, context, anticipation, optimization, goals | Store data, handle HTTP, validate inputs |
| Execution Services (Layer 1/2) | Data persistence, input validation, instruction execution, event publishing | Make autonomous decisions, apply business judgment |
| API Gateway | Authentication, routing, rate limiting | Make decisions, store data |
| Frontend | Member/operator experience, rendering AI decisions visually | Contain business logic |

---

## 4. Technology Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend services | Python, FastAPI, SQLAlchemy (async) | 26 services, identical structural pattern: `src/models`, `src/services`, `src/api`, `src/core`, `tests` |
| Database | PostgreSQL 16 (Cloud SQL), SQLite (test, `StaticPool` for isolation) | Cross-dialect UUID type decorator runs the same models against both |
| AI orchestration | Anthropic Claude + LangGraph | Supervisor + 11 specialist agents (12 total — expanded from 7, see `docs/AI_ORCHESTRATION_SCALING.md` §Roster expansion), each calling typed Python tool functions against service APIs. Scheduled as 2 tiers — realtime / batch — sharing one decision log + one Redis checkpoint store (REV D); two Anthropic Workspaces isolate rate-limit budgets per tier |
| Event streaming | Apache Kafka (Strimzi operator, self-hosted on GKE) | Canonical topic/event-type definitions in a shared module; every service imports from it |
| Frontend (web) | TypeScript, React, TanStack Start, Tailwind CSS | Member App, Operator Portal, Executive Console |
| Frontend (mobile) | Flutter | Rewrite from React Native; Riverpod + GoRouter |
| Admin prototypes | Single-file HTML | Fast-iteration prototypes for admin modules (Fleet, HRM, Dealer Center, Marketing) prior to React port |
| Cloud infrastructure | **Google Cloud Platform** — GKE + Cloud SQL | Corrected from an earlier AWS EKS/RDS draft — see §1 |
| Cache / sessions | Redis (Memorystore, `STANDARD_HA` in prod) | Rate-limit counters, pricing caches, revocation/denylist state — never the JWT itself |
| IoT / telematics | Eclipse Mosquitto (Phase 1) → EMQX (Phase 2) | MQTT over TLS, client certs keyed on VIN as Common Name |
| Auth | JWT via HttpOnly cookies only | Never localStorage; `X-Tenant-Id` header on every request |
| IaC | Terraform (GCP provider) + Kustomize | See `infrastructure/` |
| Document generation | python-docx, docx (Node.js), ReportLab, LibreOffice headless + pdftoppm | Used for deliverables like this document's companion outputs |

---

## 5. Deployment Topology (GKE)

- **One regional GKE cluster per environment** (dev / staging / prod),
  private nodes, VPC-native, Workload Identity (no downloaded JSON keys).
- **Three node pools (REV D — was two):**
  - `general-pool` — gateway, Layer 0/1/2 services. Autoscales 1–12 nodes
    (prod), `e2-standard-4`.
  - `ai-orchestration-realtime-pool` — tainted, dedicated to
    `ai-orchestration-realtime` (Supervisor, Concierge, Member Experience).
    Guaranteed QoS pods, higher PriorityClass. Autoscales 2–8 nodes (prod),
    `n2-standard-4`.
  - `ai-orchestration-batch-pool` — tainted, dedicated to
    `ai-orchestration-batch` (Fleet Intelligence, Pricing Intelligence,
    Revenue, Retention). Burstable QoS, lower PriorityClass — evicted
    first under any pressure. Autoscales 1–6 nodes (prod), `n2-standard-8`.
  - Splitting these means a runaway batch reasoning job can no longer
    starve the realtime path's CPU/memory — they no longer share a node,
    let alone a pod. See `docs/AI_ORCHESTRATION_SCALING.md`.
- **Two Cloud SQL instances per environment:** one hosting the 13 Layer
  0/1 service databases, one hosting the 12 Layer 2 service databases —
  each database has its own least-privilege user; no cross-database grants.
  (Documented as two instances rather than 26 for operational simplicity
  at this stage; any service can be split to a dedicated instance later
  with zero application-code change, since connection strings are already
  per-service.)
- **Kafka runs in-cluster** via the Strimzi operator (KRaft mode, 3
  brokers in prod) rather than a managed GCP substitute, so existing
  Kafka client code needs no changes for GCP. The AI nucleus's consumer
  group is split in two (`ai-orchestration-realtime`,
  `ai-orchestration-batch`), each subscribed only to its own topic set, so
  a batch-side backlog can't delay realtime event processing.
- **Redis** via Memorystore, `STANDARD_HA` tier in prod. Also now the
  externalized LangGraph checkpoint store (keyed by `thread_id`) for the
  AI nucleus, so any replica in either tier can service any conversation
  turn — no sticky sessions, no state lost on pod restart.
- **Ingress** via GKE's native Ingress (`kubernetes.io/ingress.class: gce`)
  with a Google-managed TLS certificate — `api-gateway` is the only backend
  it ever routes to.
- **Network policy:** default-deny ingress in the `voyqi` namespace;
  explicit allow rules open exactly `api-gateway` (from the load balancer)
  and inter-service calls from `api-gateway` / the AI orchestration
  nucleus (matched via the shared `role: ai-orchestration` label, covering
  both `ai-orchestration-realtime` and `ai-orchestration-batch`)
  only. See `infrastructure/kubernetes/base/networking/network-policies.yaml`.

---

## 6. Repository Layout

```
voyqi-infra/
├── README.md                          ← this document
├── docker-compose.yml                 ← local dev, all 26 services
├── .env.example
├── docs/
│   ├── PORT_REGISTRY.md               ← authoritative port list
│   ├── SERVICE_INVENTORY.md           ← per-service build status
│   ├── AI_ORCHESTRATION_SCALING.md    ← realtime/batch split rationale (REV D)
│   └── AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md  ← 3-tier autonomy model, hand to engineers
├── scripts/
│   ├── generate_service_manifests.py  ← clones the service template per catalog entry
│   └── init-multi-db.sh
└── infrastructure/
    ├── terraform/
    │   ├── main.tf                    ← root module, wires everything, GCP only
    │   ├── variables.tf / outputs.tf
    │   ├── environments/{dev,staging,prod}.tfvars
    │   └── modules/
    │       ├── networking/            ← VPC, subnets, Cloud NAT, firewall
    │       ├── gke/                   ← private GKE cluster, 2 node pools
    │       ├── cloudsql/               ← 2 instances, 26 per-service DBs/users
    │       ├── redis/                 ← Memorystore
    │       ├── kafka/                 ← Strimzi operator + Kafka CR
    │       └── iam/                   ← 4 GSAs, Workload Identity bindings
    └── kubernetes/
        ├── services-catalog.yaml      ← single source of truth for all 26 services
        ├── base/
        │   ├── namespace.yaml
        │   ├── networking/network-policies.yaml
        │   ├── gateway/                ← hand-authored (Ingress, cert)
        │   ├── ai-orchestration/       ← hand-authored — realtime + batch tiers, shared config, tier-policy.yaml (REV D)
        │   ├── _service-template/     ← cloneable template, do not deploy directly
        │   └── services/              ← generated — 24 services, one dir each
        └── overlays/{dev,staging,prod}/
```

---

## 7. Local Development

```bash
cp .env.example .env
docker compose up -d
# Gateway:    http://localhost:8000/health
# Kafka UI:   http://localhost:8085
```

## 8. Generating / Regenerating Service Manifests

`api-gateway`, `ai-orchestration-realtime`, and `ai-orchestration-batch`
are hand-authored (Ingress, node taints, PriorityClass). All other 24
services are generated from one template so they stay structurally
identical:

```bash
# Edit infrastructure/kubernetes/services-catalog.yaml to add/change a
# service, then:
python3 scripts/generate_service_manifests.py
kubectl apply -k infrastructure/kubernetes/overlays/dev
```

## 9. Deployment

```bash
cd infrastructure/terraform
terraform init
terraform plan  -var-file=environments/prod.tfvars
terraform apply -var-file=environments/prod.tfvars

# then
cd ../kubernetes
kubectl apply -k overlays/prod
```

---

## 10. New-Service Build Recipe

1. Add the service to `infrastructure/kubernetes/services-catalog.yaml`
   (name, port from `docs/PORT_REGISTRY.md`'s next reserved slot, layer,
   service account, status: `PLANNED`).
2. Add its database name to the appropriate list in
   `terraform/modules/cloudsql/variables.tf`.
3. Run `terraform apply` to provision its database + user.
4. Run `scripts/generate_service_manifests.py` to generate its K8s manifests.
5. Scaffold the FastAPI service itself from the backend template
   (`src/models`, `src/services`, `src/api`, `src/core`, `tests`), wire it
   to its Cloud SQL database, register its Kafka topics in the shared
   topic-schema module, register its RBAC permissions in `rbac-service` /
   `super-admin-svc`, and — if it needs a specialist agent — add that
   agent to the appropriate tier's `AGENT_ROSTER` and `KAFKA_TOPICS` env
   in `ai-orchestration-realtime` or `ai-orchestration-batch` (realtime if
   a human is waiting on the response in-session, batch otherwise — see
   `docs/AI_ORCHESTRATION_SCALING.md`).
6. Flip status to `READY` in the catalog only once tests pass — this
   repository's documentation reflects reality, not intent.

---

## 11. Recurring Bug Patterns to Guard Against

- SQLite naive datetime comparison — fix: `.replace(tzinfo=timezone.utc)`
- Double-prefix router registration
- Double `.scalar_one_or_none()` cursor consumption
- Proration double-billing
- SQLAlchemy column defaults not applying until flush
- Test isolation failures from incorrect monkeypatching targets

---

## 12. Related Documents

- `docs/PORT_REGISTRY.md` — authoritative port list
- `docs/SERVICE_INVENTORY.md` — per-service build status and notes
- `docs/AI_ORCHESTRATION_SCALING.md` — why the AI nucleus is scheduled as
  two tiers, why that isn't the same as being two services, and why full
  per-agent microservice decomposition was considered and not adopted
- `docs/AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` — the three-tier
  autonomy model replacing the blanket "AI proposes, human approves" rule,
  the required code changes, and the per-module rollout checklist
  (including the specific diff for Fleet Management's already-integrated
  code). This is the document to hand to the engineering team.
- `VOYQI_PRD_v3.docx` — the only authoritative product requirements
  document (ignore any reference to a "Mobility Wealth Platform (MWP)"
  PRD or PRD V6 — those do not apply)
- VOYQI Enterprise Administration Screen Specification — 122-screen
  catalog across 17 primary panels, all provisioned from the Super Admin
  Panel (SAP-003)
