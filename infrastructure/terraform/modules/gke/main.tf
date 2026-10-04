/**
 * VOYQI Mobility OS — GKE Module
 * Private, VPC-native regional GKE cluster hosting all 26 microservices
 * across the three architecture layers (see /docs/SERVICE_INVENTORY.md).
 *
 * THREE node pools (REV D — was two):
 *   1. general-pool                 — gateway, auth, Layer 1 execution, Layer 2 admin
 *   2. ai-realtime-pool              — Supervisor, Concierge, Member Experience
 *                                      (latency-critical, member is watching a chat window)
 *   3. ai-orchestration-batch-pool   — Fleet Intelligence, Pricing Intelligence,
 *                                      Revenue, Retention (throughput-heavy batch reasoning)
 *
 * ai-orchestration-service is still ONE logical service — same LangGraph
 * graph, same shared Postgres decision log, same Redis-backed checkpoint
 * store (see PRD §23.1: "do not build the AI as multiple separate
 * services — multiple independent AI services do not share context").
 * This split is a scheduling/isolation boundary only, so a Fleet
 * Intelligence batch job chewing through a fleet-wide reassignment
 * calculation can never starve the CPU/memory Concierge needs for a
 * real-time reply — they no longer share a node, let alone a pod.
 */

resource "google_container_cluster" "primary" {
  name     = "voyqi-${var.environment}"
  project  = var.project_id
  location = var.region

  network    = var.vpc_self_link
  subnetwork = var.gke_subnet_self_link

  # We manage node pools explicitly below.
  remove_default_node_pool = true
  initial_node_count       = 1

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # control plane reachable via authorized networks (CI/CD, engineer VPN)
    master_ipv4_cidr_block  = var.master_cidr
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.authorized_networks
      content {
        cidr_block   = cidr_blocks.value.cidr
        display_name = cidr_blocks.value.name
      }
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  release_channel {
    channel = var.environment == "prod" ? "STABLE" : "REGULAR"
  }

  addons_config {
    horizontal_pod_autoscaling { disabled = false }
    http_load_balancing { disabled = false }
    network_policy_config { disabled = false } # required for K8s NetworkPolicy tenant isolation
  }

  network_policy {
    enabled  = true
    provider = "CALICO"
  }

  maintenance_policy {
    daily_maintenance_window {
      start_time = "06:00"
    }
  }

  deletion_protection = var.environment == "prod"
}

# General-purpose pool: gateway, auth, Layer 1 execution services, Layer 2
# admin-panel-backing services.
resource "google_container_node_pool" "general" {
  name     = "general-pool"
  project  = var.project_id
  location = var.region
  cluster  = google_container_cluster.primary.name

  autoscaling {
    min_node_count = var.environment == "prod" ? 3 : 1
    max_node_count = var.environment == "prod" ? 12 : 4
  }

  node_config {
    machine_type = var.general_machine_type
    disk_size_gb = 50
    disk_type    = "pd-ssd"

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      voyqi-pool = "general"
      env        = var.environment
    }

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# AI BATCH pool: Fleet Intelligence, Pricing Intelligence, Revenue,
# Retention. Throughput-heavy batch reasoning (fleet-wide reassignment
# calculations, pricing model recalculation). Tolerant of queue depth —
# HPA below scales on CPU utilization, not latency.
resource "google_container_node_pool" "ai_pool" {
  name     = "ai-orchestration-batch-pool"
  project  = var.project_id
  location = var.region
  cluster  = google_container_cluster.primary.name

  autoscaling {
    min_node_count = var.environment == "prod" ? 2 : 1
    max_node_count = var.environment == "prod" ? 6 : 2
  }

  node_config {
    machine_type = var.ai_machine_type
    disk_size_gb = 80
    disk_type    = "pd-ssd"

    taint {
      key    = "workload"
      value  = "ai-orchestration-batch"
      effect = "NO_SCHEDULE"
    }

    labels = {
      voyqi-pool = "ai-orchestration-batch"
      env        = var.environment
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

# AI REALTIME pool: Supervisor, Concierge, Member Experience. Member is
# watching a chat window — this pool is never shared with batch reasoning
# workloads, has no autoscale-down-to-zero risk in prod, and its pods run
# Guaranteed QoS (requests == limits, set in the K8s manifest) so the
# kubelet never throttles them under node pressure.
resource "google_container_node_pool" "ai_realtime_pool" {
  name     = "ai-realtime-pool"
  project  = var.project_id
  location = var.region
  cluster  = google_container_cluster.primary.name

  autoscaling {
    min_node_count = var.environment == "prod" ? 2 : 1
    max_node_count = var.environment == "prod" ? 8 : 2
  }

  node_config {
    machine_type = var.ai_realtime_machine_type
    disk_size_gb = 50
    disk_type    = "pd-ssd"

    taint {
      key    = "workload"
      value  = "ai-orchestration-realtime"
      effect = "NO_SCHEDULE"
    }

    labels = {
      voyqi-pool = "ai-orchestration-realtime"
      env        = var.environment
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
