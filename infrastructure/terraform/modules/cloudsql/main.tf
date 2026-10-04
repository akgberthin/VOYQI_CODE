/**
 * VOYQI Mobility OS — Cloud SQL Module
 *
 * ARCHITECTURAL RULE (non-negotiable, PRD §3.1 / §13): every microservice
 * owns its own database. No shared database, no cross-service SQL join.
 *
 * "Owns its own database" is enforced at the SQL level (separate database +
 * separate least-privilege user per service), not necessarily one Cloud SQL
 * *instance* per service — that would be 26 instances to patch/monitor for
 * an early-stage platform. Instead: one Postgres instance per layer
 * (general-purpose, HA in prod), each hosting one database per service in
 * that layer, each with its own credentials and zero cross-database grants.
 * Any service can be split to its own dedicated instance later with no
 * application-code change (connection string is already per-service).
 *
 * Layer 0 + Layer 1 instance: gateway/auth + 12 core execution services
 * Layer 2 instance: 12 admin-panel-backing services
 */

resource "google_sql_database_instance" "core" {
  name             = "voyqi-${var.environment}-core-pg"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_16"

  settings {
    tier              = var.core_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = var.environment == "prod"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = var.environment == "prod" ? 30 : 7
      }
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.vpc_id
    }

    maintenance_window {
      day  = 7 # Sunday
      hour = 6
    }

    insights_config {
      query_insights_enabled = true
    }
  }

  deletion_protection = var.environment == "prod"
}

resource "google_sql_database_instance" "admin_layer" {
  name             = "voyqi-${var.environment}-admin-pg"
  project          = var.project_id
  region           = var.region
  database_version = "POSTGRES_16"

  settings {
    tier              = var.admin_tier
    availability_type = var.environment == "prod" ? "REGIONAL" : "ZONAL"
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = var.environment == "prod"
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = var.environment == "prod" ? 30 : 7
      }
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.vpc_id
    }

    insights_config {
      query_insights_enabled = true
    }
  }

  deletion_protection = var.environment == "prod"
}

# One database per Layer 0/1 service, on the core instance.
resource "google_sql_database" "core_service_db" {
  for_each = toset(var.core_service_names)
  name     = replace(each.value, "-", "_")
  instance = google_sql_database_instance.core.name
  project  = var.project_id
}

# One least-privilege user per Layer 0/1 service — scoped to its own
# database only. No service's credentials can read another service's schema.
resource "google_sql_user" "core_service_user" {
  for_each = toset(var.core_service_names)
  name     = "${replace(each.value, "-", "_")}_svc"
  instance = google_sql_database_instance.core.name
  project  = var.project_id
  password = var.service_db_passwords[each.value]
}

# One database per Layer 2 admin-panel-backing service, on the admin instance.
resource "google_sql_database" "admin_service_db" {
  for_each = toset(var.admin_service_names)
  name     = replace(each.value, "-", "_")
  instance = google_sql_database_instance.admin_layer.name
  project  = var.project_id
}

resource "google_sql_user" "admin_service_user" {
  for_each = toset(var.admin_service_names)
  name     = "${replace(each.value, "-", "_")}_svc"
  instance = google_sql_database_instance.admin_layer.name
  project  = var.project_id
  password = var.service_db_passwords[each.value]
}
