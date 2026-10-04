variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  description = "GCP region, e.g. us-east4 (closest to Fort Lauderdale HQ)"
}

variable "environment" {
  type        = string
  description = "dev | staging | prod"
}

variable "subnet_cidr" {
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  type        = string
  default     = "10.20.0.0/14"
}

variable "services_cidr" {
  type        = string
  default     = "10.30.0.0/20"
}
