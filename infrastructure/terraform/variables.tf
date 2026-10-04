variable "project_id" {
  type        = string
  description = "GCP project ID for this environment"
}

variable "region" {
  type        = string
  default     = "us-east4"
  description = "GCP region. us-east4 (N. Virginia) is closest to the Fort Lauderdale-Miami flagship hub for lowest write latency."
}

variable "environment" {
  type        = string
  description = "dev | staging | prod"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging, or prod."
  }
}

variable "general_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "ai_machine_type" {
  type    = string
  default = "n2-standard-8"
  description = "Batch-tier AI pool (Fleet Intelligence, Pricing Intelligence, Revenue, Retention)."
}

variable "ai_realtime_machine_type" {
  type    = string
  default = "n2-standard-4"
  description = "Realtime-tier AI pool (Supervisor, Concierge, Member Experience)."
}

variable "authorized_networks" {
  type = list(object({
    cidr = string
    name = string
  }))
  default = []
  description = "CIDR ranges allowed to reach the GKE control plane (engineer VPN, CI/CD runners)."
}

variable "service_db_passwords" {
  type      = map(string)
  sensitive = true
  default   = {}
  description = "Per-service Cloud SQL passwords. Supply via -var-file sourced from Secret Manager at apply time; never commit actual values."
}
