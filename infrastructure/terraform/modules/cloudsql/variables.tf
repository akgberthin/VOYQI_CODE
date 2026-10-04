variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }

variable "core_tier" {
  type    = string
  default = "db-custom-4-16384" # 4 vCPU / 16GB — Layer 0/1, 14 services
}

variable "admin_tier" {
  type    = string
  default = "db-custom-2-8192" # 2 vCPU / 8GB — Layer 2, lighter admin traffic
}

# Layer 0 (platform infra) + Layer 1 (core execution) — 2 + 12 = 14 services
variable "core_service_names" {
  type = list(string)
  default = [
    "auth-service",
    "user-service",
    "subscription-service",
    "fleet-service",
    "rental-service",
    "payment-service",
    "marketing-service",
    "notification-service",
    "global-mobility-svc",
    "pricing-service",
    "rbac-service",
    "sales-service",
    "ai-orchestration-service",
  ]
  description = "Layer 0/1 services, each gets its own database + user on the core instance. api-gateway is stateless and has no database. NOTE (REV D): ai-orchestration-service keeps ONE shared database even though its K8s deployment is now split into ai-orchestration-realtime and ai-orchestration-batch — that split is a scheduling/isolation boundary only. Both deployments read/write the same decision log here and the same Redis-backed LangGraph checkpoint store, per PRD §23.1's rule against building the AI as multiple independent, context-siloed services."
}

# Layer 2 (admin-panel-backing services) — 12 services
variable "admin_service_names" {
  type = list(string)
  default = [
    "super-admin-svc",
    "crm-svc",
    "dms-svc",
    "originations-svc",
    "executive-svc",
    "management-svc",
    "real-estate-svc",
    "capital-finance-svc",
    "insurance-risk-svc",
    "technology-ip-svc",
    "security-compliance-svc",
    "hrm-svc",
  ]
}

variable "service_db_passwords" {
  type        = map(string)
  sensitive   = true
  description = "Per-service DB password, injected via -var-file from Secret Manager / CI secrets. Never committed."
  default     = {}
}
