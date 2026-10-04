variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "vpc_self_link" { type = string }
variable "gke_subnet_self_link" { type = string }

variable "master_cidr" {
  type    = string
  default = "172.16.0.0/28"
}

variable "authorized_networks" {
  type = list(object({
    cidr = string
    name = string
  }))
  default = []
}

variable "general_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "ai_machine_type" {
  type    = string
  default = "n2-standard-8"
  description = "Batch-tier AI pool — Fleet Intelligence, Pricing Intelligence, Revenue, Retention."
}

variable "ai_realtime_machine_type" {
  type    = string
  default = "n2-standard-4"
  description = "Realtime-tier AI pool — Supervisor, Concierge, Member Experience. Smaller, more replicas, latency over batch throughput."
}
