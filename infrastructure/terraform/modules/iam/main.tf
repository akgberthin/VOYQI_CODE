/**
 * VOYQI Mobility OS — IAM Module
 * Google Service Accounts bound to Kubernetes Service Accounts via Workload
 * Identity — no downloaded JSON key files anywhere in the platform.
 * One GSA per architecture layer (not per-microservice) to keep IAM
 * reviewable; fine-grained data isolation is enforced at the Cloud SQL
 * user + X-Tenant-Id layer, not at the GCP IAM layer.
 */

resource "google_service_account" "gateway" {
  account_id   = "voyqi-${var.environment}-gateway"
  display_name = "VOYQI API Gateway (${var.environment})"
  project      = var.project_id
}

resource "google_service_account" "core_services" {
  account_id   = "voyqi-${var.environment}-core-svc"
  display_name = "VOYQI Layer 0/1 execution services (${var.environment})"
  project      = var.project_id
}

resource "google_service_account" "ai_orchestration" {
  account_id   = "voyqi-${var.environment}-ai-orch"
  display_name = "VOYQI AI Orchestration nucleus (${var.environment})"
  project      = var.project_id
}

resource "google_service_account" "admin_services" {
  account_id   = "voyqi-${var.environment}-admin-svc"
  display_name = "VOYQI Layer 2 admin-panel-backing services (${var.environment})"
  project      = var.project_id
}

# Cloud SQL client role for services that talk to Cloud SQL over the
# Cloud SQL Auth Proxy sidecar.
resource "google_project_iam_member" "core_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.core_services.email}"
}

resource "google_project_iam_member" "admin_sql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.admin_services.email}"
}

# AI orchestration additionally needs Secret Manager access for the
# Anthropic API key, and pub/sub-equivalent (Kafka runs in-cluster, so no
# GCP pub/sub role needed — this is a placeholder for future managed Kafka).
resource "google_project_iam_member" "ai_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.ai_orchestration.email}"
}

resource "google_project_iam_member" "core_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.core_services.email}"
}

resource "google_project_iam_member" "admin_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.admin_services.email}"
}

# Workload Identity bindings: KSA -> GSA per namespace/KSA name.
resource "google_service_account_iam_member" "gateway_wi" {
  service_account_id = google_service_account.gateway.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "serviceAccount:${var.project_id}.svc.id.goog[voyqi/gateway-ksa]"
}

resource "google_service_account_iam_member" "core_wi" {
  service_account_id = google_service_account.core_services.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "serviceAccount:${var.project_id}.svc.id.goog[voyqi/core-svc-ksa]"
}

resource "google_service_account_iam_member" "ai_wi" {
  service_account_id = google_service_account.ai_orchestration.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "serviceAccount:${var.project_id}.svc.id.goog[voyqi/ai-orch-ksa]"
}

resource "google_service_account_iam_member" "admin_wi" {
  service_account_id = google_service_account.admin_services.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "serviceAccount:${var.project_id}.svc.id.goog[voyqi/admin-svc-ksa]"
}
