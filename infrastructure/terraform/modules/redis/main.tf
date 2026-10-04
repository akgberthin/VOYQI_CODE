/**
 * VOYQI Mobility OS — Redis Module
 * GCP Memorystore for Redis. Used for: rate-limiting counters at the API
 * gateway, pricing-service demand-multiplier caches, session lookups
 * (JWT itself lives in an HttpOnly cookie — Redis never stores the token,
 * only revocation/denylist state and short-TTL derived data).
 */

resource "google_redis_instance" "cache" {
  name           = "voyqi-${var.environment}-cache"
  project        = var.project_id
  region         = var.region
  tier           = var.environment == "prod" ? "STANDARD_HA" : "BASIC"
  memory_size_gb = var.memory_size_gb
  redis_version  = "REDIS_7_2"

  authorized_network      = var.vpc_id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  transit_encryption_mode = "SERVER_AUTHENTICATION"
  auth_enabled            = true
}
