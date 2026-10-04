# VOYQI Mobility OS — Service Inventory & Build Status

26 microservices across 3 architecture layers. Source of truth:
`infrastructure/kubernetes/services-catalog.yaml`. This file is the
human-readable view; regenerate manifests from the catalog, not from this
doc.

## Layer 0 — Platform Infrastructure (2 services)

| Service | Port | Status | Notes |
|---|---|---|---|
| api-gateway | 8000 | READY | Sole ingress. JWT validation, rate limiting, reverse proxy. |
| auth-service | 8001 | READY | JWT via HttpOnly cookies, MFA, sessions. |

## Layer 1 — Core Execution Services (12 services)

| Service | Port | Status | Notes |
|---|---|---|---|
| user-service | 8002 | READY | Profiles, loyalty, behavior data. |
| subscription-service | 8003 | READY | Lifecycle for all 8 mobility classes. |
| fleet-service | 8004 | READY | Backs the completed Fleet Management UI — 13 sub-modules (FLT-001–013), 887+ passing tests. |
| rental-service | 8005 | READY | Short-term rental booking and pricing. |
| payment-service | 8006 | READY | Stripe charges, invoices, deposit holds. |
| marketing-service | 8007 | READY | Campaign execution, CRM feed. |
| sales-service | 8009 | READY | Dealership origination execution. |
| pricing-service | 8010 | READY | Dynamic pricing, demand multipliers. |
| rbac-service | 8011 | READY | Role/permission execution engine. |
| notification-service | 8012 | READY | Email, SMS, push delivery. |
| global-mobility-svc | 8013 | READY | Hub network, VOYQI Card, cross-city continuity. |
| ai-orchestration-realtime | 8020 | READY | The nucleus, realtime tier. Supervisor + Member Experience + Concierge. Latency-critical — Guaranteed QoS, dedicated node pool, higher PriorityClass, latency-based HPA. REV D split — see `AI_ORCHESTRATION_SCALING.md`. |
| ai-orchestration-batch | 8014 | READY | The nucleus, batch tier. Fleet Intelligence + Pricing Intelligence + Revenue + Retention. Throughput-heavy — Burstable QoS, own node pool, CPU-based HPA, eligible for Anthropic's async Batch API on non-time-critical jobs. Shares the same Postgres decision log and Redis checkpoint store as the realtime tier — still one logical service (PRD §23.1). |

## Layer 2 — Admin-Panel-Backing Services (12 services)

Introduced to back the 17 primary panels in the Enterprise Admin Screen
Specification (122 total screens: 63 P0, 52 P1, 7 P2), all provisioned
exclusively from the Super Admin Panel (SAP-003 Panel & Module Distribution
Console).

| Service | Port | Status | Backs |
|---|---|---|---|
| super-admin-svc | 8021 | ACTIVE_DEV | Super Admin Panel — RBAC/ABAC authoring, panel distribution, global directory, CMS, audit (SAP-001–007). |
| crm-svc | 8022 | PLANNED | AI-Native Enterprise CRM — 360 view, lead engine, conversational hub. |
| dms-svc | 8023 | PLANNED | Dealer Center Management System — rooftop, inventory, deal desk, F&I. |
| originations-svc | 8024 | PLANNED | Sales & Originations — pipeline, decisioning, lender mgmt, e-contracting. |
| executive-svc | 8025 | PLANNED | Executive Command Center — cross-subsidiary dashboards. |
| management-svc | 8026 | PLANNED | Management (shared services) panel. |
| real-estate-svc | 8027 | PLANNED | Real Estate panel — hub property portfolio. |
| capital-finance-svc | 8028 | PLANNED | Capital & Finance panel. |
| insurance-risk-svc | 8029 | PLANNED | Insurance & Risk panel. |
| technology-ip-svc | 8030 | PLANNED | Technology & IP panel. |
| security-compliance-svc | 8031 | PLANNED | Security, Identity & Compliance (SEC-001–005). |
| hrm-svc | 8101 | ACTIVE_DEV | HRM (AI-Native Talent CRM). Academy (HRM-006) and Notices & Communication (HRM-008) in active development; single-file prototype at `voyqi-hrm.html`. Open item: wiring live Anthropic Messages API calls into the communication and academy panels (currently deterministic-only). |

## Cross-cutting architectural rules (enforced across all 26)

1. **AI is the nucleus, not a feature.** Execution services persist,
   validate, and execute; ai-orchestration-service decides. Neither does
   the other's job.
2. **Database-per-service.** No shared databases, no cross-service SQL
   joins. Enforced at the Cloud SQL user level (`terraform/modules/cloudsql`).
3. **Gateway-only ingress.** No service port is ever reachable directly
   from a client — enforced by `NetworkPolicy` (`base/networking/`), not
   just convention.
4. **Every request carries `X-Tenant-Id`.** Every table in every service
   is tenant-scoped, mirroring the VOYQI Holdings legal-entity structure.
5. **AI-initiated calls are marked.** ai-orchestration-service identifies
   itself with `X-AI-Initiated` so audit logs distinguish AI-driven from
   human-driven actions.
6. **Two-layer agent discipline.** Every AI judgment has a deterministic
   fallback implemented in the execution service itself — the platform
   never has a single point of AI-dependent failure.
7. **"AI proposes, human approves"** is the UX rule for AI-assisted flows.
8. **Every state change publishes a Kafka event.** Topic/event-type
   schemas are defined for every service in a shared module — no
   hardcoded topic strings.
