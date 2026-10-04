output "vpc_id" {
  value = google_compute_network.vpc.id
}

output "vpc_self_link" {
  value = google_compute_network.vpc.self_link
}

output "gke_subnet_id" {
  value = google_compute_subnetwork.gke_subnet.id
}

output "gke_subnet_self_link" {
  value = google_compute_subnetwork.gke_subnet.self_link
}

output "private_vpc_connection" {
  value = google_service_networking_connection.private_vpc_connection.id
}
