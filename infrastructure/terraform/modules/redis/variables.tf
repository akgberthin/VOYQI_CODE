variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "vpc_id" { type = string }

variable "memory_size_gb" {
  type    = number
  default = 4
}
