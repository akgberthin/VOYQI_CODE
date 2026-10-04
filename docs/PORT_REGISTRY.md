# VOYQI Mobility OS — Port Registry

Authoritative. If a port is needed for a new service, add it here first,
then to `infrastructure/kubernetes/services-catalog.yaml`.

| Port | Service | Layer | Status |
|---|---|---|---|
| 8000 | api-gateway | 0 — Platform Infrastructure | READY |
| 8001 | auth-service | 0 — Platform Infrastructure | READY |
| 8002 | user-service | 1 — Core Execution | READY |
| 8003 | subscription-service | 1 — Core Execution | READY |
| 8004 | fleet-service | 1 — Core Execution | READY |
| 8005 | rental-service | 1 — Core Execution | READY |
| 8006 | payment-service | 1 — Core Execution | READY |
| 8007 | marketing-service | 1 — Core Execution | READY |
| 8008 | *(reserved)* | — | — |
| 8009 | sales-service | 1 — Core Execution | READY |
| 8010 | pricing-service | 1 — Core Execution | READY |
| 8011 | rbac-service | 1 — Core Execution | READY |
| 8012 | notification-service | 1 — Core Execution | READY |
| 8013 | global-mobility-svc | 1 — Core Execution | READY |
| 8014 | ai-orchestration-batch | 1 — AI Nucleus (batch tier) | READY |
| 8015–8019 | *(reserved for future core execution services)* | — | — |
| 8020 | ai-orchestration-realtime | 1 — AI Nucleus (realtime tier) | READY |
| 8021 | super-admin-svc | 2 — Admin-Panel-Backing | ACTIVE_DEV |
| 8022 | crm-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8023 | dms-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8024 | originations-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8025 | executive-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8026 | management-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8027 | real-estate-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8028 | capital-finance-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8029 | insurance-risk-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8030 | technology-ip-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8031 | security-compliance-svc | 2 — Admin-Panel-Backing | PLANNED |
| 8085 | kafka-ui *(local dev only)* | tooling | — |
| 8100 | *(reserved — talent-svc functions consolidated into hrm-svc)* | 2 | — |
| 8101 | hrm-svc | 2 — Admin-Panel-Backing | ACTIVE_DEV |

**26 total logical services** — 2 Layer 0, 12 Layer 1 (AI orchestration counted once, split across 2 ports/deployments), 12 Layer 2.

**REV D note:** `ai-orchestration-realtime` (8020) and `ai-orchestration-batch` (8014) are two K8s Deployments and two ports, but ONE logical service — same codebase, same shared Postgres decision log, same Redis-backed LangGraph checkpoint store. The split exists purely for node-pool isolation, deploy blast radius, and Anthropic Workspace rate-limit separation. See `docs/AI_ORCHESTRATION_SCALING.md`.

Note: `hrm-svc` sits outside the 8021–8031 Layer 2 block at 8101 — a
historical allocation carried forward rather than renumbered, to avoid
invalidating existing HRM module references (`voyqi-hrm.html`, HRM-006,
HRM-008 work already in flight).
