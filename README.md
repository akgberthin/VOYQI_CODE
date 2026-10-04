# VOYQI Mobility OS

Multi-tenant mobility platform: 25 FastAPI microservices, an AI orchestration
nucleus (Anthropic Claude), Kafka event streaming, and a database per service.

## Layout

| Path | Contents |
|---|---|
| `services/` | All backend microservices (FastAPI + async SQLAlchemy + Alembic + pytest) |
| `docker-compose.yml` | Local environment: every service, one Postgres per service, Kafka |
| `infrastructure/` | Terraform (GCP: GKE, Cloud SQL, Redis, Kafka, IAM) and Kubernetes/Kustomize manifests |
| `scripts/` | Manifest generator and DB init helpers |
| `docs/` | Architecture, service inventory, port registry, autonomy tiering, AI scaling |
| `docs/implementation-guides/` | Per-module implementation and installation guides |
| `prototypes/` | Single-file HTML UI prototypes (design reference only, not production frontend) |

## Run locally

```bash
cp .env.example .env
docker compose up -d --build
# Gateway: http://localhost:8000/health
```

## Test a service

```bash
cd services/<service-name>
pip install -r requirements.txt
pytest
```

Start with `docs/ARCHITECTURE_AND_INFRASTRUCTURE.md`.
