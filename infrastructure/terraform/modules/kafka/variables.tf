variable "environment" { type = string }

variable "strimzi_chart_version" {
  type    = string
  default = "0.43.0"
}

variable "kafka_version" {
  type    = string
  default = "3.8.0"
}

variable "broker_count" {
  type    = number
  default = 3
}

variable "broker_disk_size" {
  type    = string
  default = "100Gi"
}
