/**
 * VOYQI Mobility OS — Networking Module
 * VPC, subnetworks, Cloud Router + Cloud NAT for private GKE egress.
 * One VPC per environment (dev / staging / prod), peered to Cloud SQL via
 * private service access.
 */

resource "google_compute_network" "vpc" {
  name                    = "voyqi-${var.environment}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  project                 = var.project_id
}

resource "google_compute_subnetwork" "gke_subnet" {
  name          = "voyqi-${var.environment}-gke-subnet"
  ip_cidr_range = var.subnet_cidr
  region        = var.region
  network       = google_compute_network.vpc.id
  project       = var.project_id

  # Secondary ranges required for VPC-native GKE (pods / services)
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }

  private_ip_google_access = true
}

# Reserved range for Cloud SQL private service access (peered, not exposed
# to the public internet — every Cloud SQL instance in this platform is
# private-IP only).
resource "google_compute_global_address" "private_service_range" {
  name          = "voyqi-${var.environment}-sql-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.vpc.id
  project       = var.project_id
}

resource "google_service_networking_connection" "private_vpc_connection" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_service_range.name]
}

resource "google_compute_router" "router" {
  name    = "voyqi-${var.environment}-router"
  region  = var.region
  network = google_compute_network.vpc.id
  project = var.project_id
}

# Cloud NAT — outbound-only internet for private GKE nodes (image pulls,
# Kafka/Anthropic API calls out through api-gateway's egress path, etc.)
resource "google_compute_router_nat" "nat" {
  name                               = "voyqi-${var.environment}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  project                            = var.project_id
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Firewall: allow intra-VPC traffic between GKE nodes/pods only. All ingress
# from outside the VPC must pass through the Ingress/Load Balancer -> the
# API Gateway. No service is ever directly internet-reachable.
resource "google_compute_firewall" "allow_internal" {
  name    = "voyqi-${var.environment}-allow-internal"
  network = google_compute_network.vpc.id
  project = var.project_id

  allow {
    protocol = "tcp"
  }
  allow {
    protocol = "udp"
  }
  allow {
    protocol = "icmp"
  }

  source_ranges = [var.subnet_cidr, var.pods_cidr, var.services_cidr]
}
