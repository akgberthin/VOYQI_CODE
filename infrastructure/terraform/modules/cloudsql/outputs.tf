output "core_instance_connection_name" {
  value = google_sql_database_instance.core.connection_name
}

output "admin_instance_connection_name" {
  value = google_sql_database_instance.admin_layer.connection_name
}

output "core_instance_private_ip" {
  value = google_sql_database_instance.core.private_ip_address
}

output "admin_instance_private_ip" {
  value = google_sql_database_instance.admin_layer.private_ip_address
}
