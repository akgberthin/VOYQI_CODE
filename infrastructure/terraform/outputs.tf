output "gke_cluster_name" {
  value = module.gke.cluster_name
}

output "ai_batch_pool_name" {
  value = module.gke.ai_batch_pool_name
}

output "ai_realtime_pool_name" {
  value = module.gke.ai_realtime_pool_name
}

output "core_sql_connection_name" {
  value = module.cloudsql.core_instance_connection_name
}

output "admin_sql_connection_name" {
  value = module.cloudsql.admin_instance_connection_name
}

output "redis_host" {
  value = module.redis.host
}

output "gateway_service_account" {
  value = module.iam.gateway_sa_email
}

output "kubectl_connect_command" {
  value = "gcloud container clusters get-credentials ${module.gke.cluster_name} --region ${var.region} --project ${var.project_id}"
}
