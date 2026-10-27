# Deploying to GCP (GKE)

How a release reaches the dev, staging and prod GKE clusters, and what
has to exist before it can. Architecture background is in
`ARCHITECTURE_AND_INFRASTRUCTURE.md`; this is the operator checklist.

## What is generated, and from where

| File | Source | Regenerate with |
|---|---|---|
| `infrastructure/kubernetes/base/services/*` | `services-catalog.yaml` + `_service-template/` | `python3 scripts/generate_service_manifests.py` |
| `overlays/<env>/kustomization.yaml` `images:` block | the catalog | same |
| `overlays/<env>/service-config.yaml` (Cloud SQL instance, Kafka bootstrap per service) | the catalog + each service's `env_prefix` | same |
| `overlays/<env>/workload-identity-patch.yaml` | GSA names in `terraform/modules/iam` | same |
| `infrastructure/kubernetes/base/kafka/topics.yaml` | every `publish_event` call in `services/` | `python3 scripts/generate_kafka_topics.py` |
| `infrastructure/kubernetes/base/mqtt/broker-config.yaml` | `infrastructure/mqtt/mosquitto.conf` + `acl` | `python3 scripts/generate_service_manifests.py` |

CI fails if any of these are out of date. Kubernetes Service names equal
the hostnames the code calls (`auth-svc:8024`, `ai-orchestration-svc:8020`, …),
so a service is renamed in the code and the catalog together or not at all.

## One-time setup per environment

1. **Infrastructure.** Fill `authorized_networks` in
   `terraform/environments/<env>.tfvars` (CI runner and admin bastion
   ranges), then with per-service database passwords:

   ```bash
   cd infrastructure/terraform
   terraform init
   terraform apply -var-file=environments/<env>.tfvars -var='service_db_passwords={"auth-svc":"...", ...}'
   ```

   This creates the VPC, the private GKE cluster (3 node pools), two Cloud
   SQL instances with one database and user per service (`auth-svc` →
   database `auth_svc`, user `auth_svc_svc`), Memorystore Redis, Kafka
   (Strimzi), and the four Workload Identity service accounts.

2. **Secrets.** One Kubernetes Secret per service in namespace `voyqi`,
   named `<service>-secrets` (`ai-orchestration-realtime-secrets` and
   `ai-orchestration-batch-secrets` for the AI tiers). Keys are environment
   variable names; every service reads its own prefix. Store the values in
   Secret Manager and sync them (External Secrets Operator or a CI step) —
   never commit them.

   | Secret | Keys |
   |---|---|
   | every service with a database | `<PREFIX>DATABASE_URL` = `postgresql+asyncpg://<db>_svc:<password>@127.0.0.1:5432/<db>` (the Cloud SQL proxy sidecar listens on 127.0.0.1) |
   | `auth-svc-secrets` | `AUTH_JWT_SECRET`, `AUTH_REDIS_URL`, `AUTH_COOKIE_DOMAIN` |
   | `gateway-secrets` | `GATEWAY_JWT_SECRET` (same value as `AUTH_JWT_SECRET`), `GATEWAY_REDIS_URL` |
   | `ai-orchestration-realtime-secrets` | `AI_ORCH_DATABASE_URL`, `AI_ORCH_ANTHROPIC_API_KEY` (realtime Workspace key; a tenant's own key from Integrations overrides it), `AI_ORCH_INTERNAL_TOKEN` |
   | `ai-orchestration-batch-secrets` | `AI_ORCH_DATABASE_URL`, `AI_ORCH_ANTHROPIC_API_KEY` (batch Workspace key), `AI_ORCH_INTERNAL_TOKEN` |
   | `rbac-svc-secrets` | `RBAC_DATABASE_URL`, `RBAC_INTEGRATIONS_KEY` (Fernet key that encrypts the credentials entered under super admin → Integrations; losing it makes them unreadable), `RBAC_INTERNAL_TOKEN` |
   | `notification-svc-secrets` | `NOTIF_DATABASE_URL`, `NOTIF_INTERNAL_TOKEN` (same value as `RBAC_INTERNAL_TOKEN`) |
   | `marketing-svc-secrets` | `MARKETING_DATABASE_URL`, `MARKETING_INTERNAL_TOKEN` (same value), `MARKETING_PUBLIC_BASE_URL` (the gateway's public https URL — Instagram and Facebook download post images from it) |
   | `fleet-svc-secrets` | `FLEET_DATABASE_URL`, `FLEET_MQTT_PASSWORD` (the `fleet-svc` user in `mqtt-broker-passwords`) |
   | `payment-svc-secrets` | `PAYMENT_STRIPE_API_KEY`, `PAYMENT_STRIPE_WEBHOOK_SECRET` |
   | (provider credentials) | SMTP, Twilio, FCM, Meta, LinkedIn, X, OpenAI, Stripe and per-tenant Anthropic keys are entered in the super admin console → Integrations, encrypted with `RBAC_INTEGRATIONS_KEY`. `NOTIF_SMTP_*` env settings remain as a platform-wide email fallback. |

   Prefixes: `AUTH_` auth-svc · `USER_` user-svc · `SUBSCRIPTION_` · `FLEET_` ·
   `RENTAL_` · `PAYMENT_` · `MARKETING_` · `SALES_` sales-service · `PRICING_` ·
   `RBAC_` · `NOTIF_` notification-svc · `HUB_` · `CRM_` · `DMS_` · `ORIG_`
   originations-svc · `EXEC_` executive-svc · `MGMT_` management-svc ·
   `REALESTATE_` · `CAPFIN_` capital-finance-svc · `RISK_` insurance-risk-svc ·
   `IP_` ip-asset-svc · `SECCOMP_` security-compliance-svc · `HRM_` ·
   `AI_ORCH_` ai-orchestration-svc · `GATEWAY_` api-gateway.

   Memorystore has AUTH and in-transit encryption on, so the Redis URL is
   `rediss://:<auth-string>@<host>:6378/0`. Kafka bootstrap and the Cloud
   SQL instance are not secrets — they come from `service-config.yaml`.

3. **Telematics broker.** Devices authenticate with a client certificate
   whose Common Name is the vehicle's VIN, issued by a device CA at
   provisioning (`ProvisioningRecord.tls_cert_common_name`); the broker
   ACL lets each device publish only to
   `voyqi/<tenant>/vehicles/<its VIN>/telemetry|tamper`. Create:

   ```bash
   # broker cert (CN/SAN = the mqtt-devices load balancer's DNS name) + the device CA
   kubectl -n voyqi create secret generic mqtt-broker-tls \
     --from-file=ca.crt=device-ca.crt --from-file=tls.crt=broker.crt --from-file=tls.key=broker.key
   # fleet-svc's login on the in-cluster listener (same password in fleet-svc-secrets)
   docker run --rm -v "$PWD":/p eclipse-mosquitto:2.1.2-alpine mosquitto_passwd -b -c /p/passwd fleet-svc '<password>'
   kubectl -n voyqi create secret generic mqtt-broker-passwords --from-file=passwd=passwd
   ```

   Certificates need the standard extensions (CA: `basicConstraints
   CA:TRUE`, `keyUsage keyCertSign`; leaves: `authorityKeyIdentifier`,
   `extendedKeyUsage serverAuth|clientAuth`) — modern TLS clients reject
   certificates without them. Point a DNS name at the `mqtt-devices`
   LoadBalancer (port 8883) and configure units with it.

4. **Deploy credentials for CI.** Create a Workload Identity Federation
   provider for the GitHub repository and a deploy service account with
   Artifact Registry Writer and Kubernetes Engine Developer. In the GitHub
   environment (`dev`/`staging`/`prod`) set `vars.GCP_PROJECT_ID`,
   `vars.GKE_CLUSTER` (`voyqi-<env>`), `secrets.GCP_WIF_PROVIDER` and
   `secrets.GCP_DEPLOY_SA`. The cluster's control plane only accepts
   authorized networks, so the deploy job runs on a self-hosted runner in
   that range (or the runner's egress range is added to it).

## Every release

Run the **Deploy** workflow (Actions → Deploy → environment). It:

1. builds every service image and pushes it as `:<env tag>` (`dev`,
   `staging`, `stable` for prod) and `:<commit sha>`;
2. applies `overlays/<env>/kafka` — topics first, so nothing publishes to a
   missing topic (auto-creation is off);
3. applies `overlays/<env>`;
4. restarts every deployment onto the new images and waits for each.

Each container runs `alembic upgrade head` before serving; replicas take
turns through a Postgres advisory lock, so a rollout migrates once.

Manually, the same is:

```bash
kubectl apply -k infrastructure/kubernetes/overlays/<env>/kafka
kubectl apply -k infrastructure/kubernetes/overlays/<env>
```

**Rollback:** point the environment tag back at the previous commit's
image (`gcloud artifacts docker tags add …:<old sha> …:<env tag>`) and
restart the deployments. Migrations are forward-only; a release whose
migration must be undone needs `alembic downgrade` run deliberately.

## After the first deploy

- `python3 scripts/seed_demo.py --host <gateway>` only for a demo tenant —
  never against real tenants.
- Before the four Fleet actions promoted to tier 1 go live, pull their
  historical approval/override rates
  (`AUTONOMY_TIERING_ENGINEER_INSTRUCTIONS.md` §2).
- `GET /api/v1/ai/audit/tier-graduation` is the evidence for later
  promotions; tier 3 is never promoted.
