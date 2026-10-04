/**
 * VOYQI Mobility OS — Root Terraform Configuration
 * Cloud provider: Google Cloud Platform (GKE + Cloud SQL).
 *
 * NOTE ON HISTORY: an earlier draft of this infrastructure targeted AWS
 * (EKS + RDS). The platform is deployed on GCP — this configuration is the
 * corrected, authoritative source. Do not reintroduce AWS provider blocks.
 */

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.40"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.31"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.14"
    }
  }

  backend "gcs" {
    bucket = "voyqi-terraform-state" # created once, out-of-band, per GCP project
    prefix = "infra"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

provider "kubernetes" {
  host                   = "https://${module.gke.cluster_endpoint}"
  cluster_ca_certificate = base64decode(module.gke.cluster_ca_certificate)
  token                  = data.google_client_config.default.access_token
}

provider "helm" {
  kubernetes {
    host                   = "https://${module.gke.cluster_endpoint}"
    cluster_ca_certificate = base64decode(module.gke.cluster_ca_certificate)
    token                  = data.google_client_config.default.access_token
  }
}

data "google_client_config" "default" {}

module "networking" {
  source      = "./modules/networking"
  project_id  = var.project_id
  region      = var.region
  environment = var.environment
}

module "gke" {
  source                    = "./modules/gke"
  project_id                = var.project_id
  region                    = var.region
  environment               = var.environment
  vpc_self_link             = module.networking.vpc_self_link
  gke_subnet_self_link      = module.networking.gke_subnet_self_link
  authorized_networks       = var.authorized_networks
  general_machine_type      = var.general_machine_type
  ai_machine_type           = var.ai_machine_type
  ai_realtime_machine_type  = var.ai_realtime_machine_type
}

module "cloudsql" {
  source                = "./modules/cloudsql"
  project_id            = var.project_id
  region                = var.region
  environment           = var.environment
  vpc_id                = module.networking.vpc_id
  service_db_passwords  = var.service_db_passwords

  depends_on = [module.networking]
}

module "redis" {
  source      = "./modules/redis"
  project_id  = var.project_id
  region      = var.region
  environment = var.environment
  vpc_id      = module.networking.vpc_id
}

module "iam" {
  source      = "./modules/iam"
  project_id  = var.project_id
  environment = var.environment
}

module "kafka" {
  source      = "./modules/kafka"
  environment = var.environment

  depends_on = [module.gke]
}
