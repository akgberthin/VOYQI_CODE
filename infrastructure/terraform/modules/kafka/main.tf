/**
 * VOYQI Mobility OS — Kafka Module
 *
 * Every service publishes state-change events; the AI orchestration nucleus
 * (port 8020) consumes ALL of them (PRD §3.1, §13.1). Rather than a managed
 * GCP pub/sub substitute, the platform runs real Kafka in-cluster via the
 * Strimzi operator so the existing Kafka client code / topic-schema module
 * in every microservice needs zero changes for GCP.
 *
 * This module installs the Strimzi operator and a 3-broker Kafka cluster
 * (KRaft mode, no separate ZooKeeper) into the `kafka` namespace. Topic
 * creation itself is declarative and owned by each service's Helm/Kustomize
 * manifest (KafkaTopic CRD), not centralized here.
 */

resource "helm_release" "strimzi_operator" {
  name             = "strimzi-kafka-operator"
  repository       = "https://strimzi.io/charts/"
  chart            = "strimzi-kafka-operator"
  version          = var.strimzi_chart_version
  namespace        = "kafka"
  create_namespace = true

  set {
    name  = "watchAnyNamespace"
    value = "true"
  }
}

resource "kubernetes_manifest" "kafka_cluster" {
  manifest = {
    apiVersion = "kafka.strimzi.io/v1beta2"
    kind       = "Kafka"
    metadata = {
      name      = "voyqi-${var.environment}"
      namespace = "kafka"
    }
    spec = {
      kafka = {
        version  = var.kafka_version
        replicas = var.broker_count
        listeners = [
          {
            name = "plain"
            port = 9092
            type = "internal"
            tls  = false
          },
          {
            name = "tls"
            port = 9093
            type = "internal"
            tls  = true
          }
        ]
        config = {
          "offsets.topic.replication.factor"         = var.broker_count
          "transaction.state.log.replication.factor" = var.broker_count
          "transaction.state.log.min.isr"             = max(1, var.broker_count - 1)
          "default.replication.factor"                = var.broker_count
          "min.insync.replicas"                        = max(1, var.broker_count - 1)
          "auto.create.topics.enable"                  = false # every topic is schema-declared, none implicit
        }
        storage = {
          type = "jbod"
          volumes = [
            {
              id          = 0
              type        = "persistent-claim"
              size        = var.broker_disk_size
              class       = "premium-rwo"
              deleteClaim = false
            }
          ]
        }
        resources = {
          requests = { cpu = "500m", memory = "2Gi" }
          limits   = { cpu = "2", memory = "4Gi" }
        }
      }
      entityOperator = {
        topicOperator = {}
        userOperator  = {}
      }
    }
  }

  depends_on = [helm_release.strimzi_operator]
}
