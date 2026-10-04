output "gateway_sa_email" {
  value = google_service_account.gateway.email
}
output "core_services_sa_email" {
  value = google_service_account.core_services.email
}
output "ai_orchestration_sa_email" {
  value = google_service_account.ai_orchestration.email
}
output "admin_services_sa_email" {
  value = google_service_account.admin_services.email
}
