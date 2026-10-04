output "cluster_name" {
  value = google_container_cluster.primary.name
}

output "ai_batch_pool_name" {
  value = google_container_node_pool.ai_pool.name
}

output "ai_realtime_pool_name" {
  value = google_container_node_pool.ai_realtime_pool.name
}

output "cluster_endpoint" {
  value     = google_container_cluster.primary.endpoint
  sensitive = true
}

output "cluster_ca_certificate" {
  value     = google_container_cluster.primary.master_auth[0].cluster_ca_certificate
  sensitive = true
}

output "workload_pool" {
  value = google_container_cluster.primary.workload_identity_config[0].workload_pool
}
