#!/usr/bin/env python3
"""
VOYQI Mobility OS — Service Manifest Generator

Reads infrastructure/kubernetes/services-catalog.yaml (the single source of
truth for all 26 microservices) and clones
infrastructure/kubernetes/base/_service-template/deployment.yaml into
infrastructure/kubernetes/base/services/<name>/ for every service, filling
in name/port/layer/service-account/replica-count placeholders.

api-gateway, ai-orchestration-realtime, and ai-orchestration-batch are
skipped — they are
hand-authored (base/gateway/, base/ai-orchestration/) because they carry
platform-specific concerns (Ingress, node-pool taints) the generic template
doesn't cover.

Usage:
    python3 scripts/generate_service_manifests.py
    kubectl apply -k infrastructure/kubernetes/overlays/dev
"""
import pathlib
import sys

try:
    import yaml
except ImportError:
    sys.exit("Missing dependency: pip install pyyaml --break-system-packages")

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "infrastructure" / "kubernetes" / "services-catalog.yaml"
TEMPLATE_PATH = ROOT / "infrastructure" / "kubernetes" / "base" / "_service-template" / "deployment.yaml"
OUTPUT_DIR = ROOT / "infrastructure" / "kubernetes" / "base" / "services"

HAND_AUTHORED = {"api-gateway", "ai-orchestration-realtime", "ai-orchestration-batch"}
# Images of the hand-authored deployments (one image can back two
# deployments: the AI nucleus runs as realtime and batch).
HAND_AUTHORED_IMAGES = ["api-gateway-svc", "ai-orchestration-svc"]
OVERLAYS_DIR = ROOT / "infrastructure" / "kubernetes" / "overlays"
REGISTRY = "us-east4-docker.pkg.dev"

REPLICA_DEFAULTS = {
    "READY": (2, 8),
    "ACTIVE_DEV": (1, 4),
    "PLANNED": (1, 2),
}


def render(template: str, service: dict) -> str:
    min_replicas, max_replicas = REPLICA_DEFAULTS[service["status"]]
    return (
        template
        .replace("__SERVICE_NAME__", service["name"])
        .replace("__LAYER__", str(service["layer"]))
        .replace("__PORT__", str(service["port"]))
        .replace("__SERVICE_ACCOUNT__", service["service_account"])
        .replace("__REPLICAS__", str(min_replicas))
        .replace("__MAX_REPLICAS__", str(max_replicas))
    )


def main() -> None:
    catalog = yaml.safe_load(CATALOG_PATH.read_text())
    template = TEMPLATE_PATH.read_text()

    layer_map = {
        "layer_0_platform_infrastructure": 0,
        "layer_1_core_execution_services": 1,
        "layer_2_admin_panel_backing_services": 2,
    }

    generated, skipped = [], []

    for section, layer in layer_map.items():
        for svc in catalog.get(section, []):
            if svc["name"] in HAND_AUTHORED:
                skipped.append(svc["name"])
                continue
            svc["layer"] = layer
            out_dir = OUTPUT_DIR / svc["name"]
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "deployment.yaml").write_text(render(template, svc))

            kustomization = (
                "apiVersion: kustomize.config.k8s.io/v1beta1\n"
                "kind: Kustomization\n"
                "resources:\n"
                "  - deployment.yaml\n"
            )
            (out_dir / "kustomization.yaml").write_text(kustomization)
            generated.append(svc["name"])

    # Top-level kustomization aggregating every generated service, so
    # overlays/{dev,staging,prod} can reference one path.
    all_dirs = sorted(generated)
    services_kustomization = (
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n" + "".join(f"  - {name}\n" for name in all_dirs)
    )
    (OUTPUT_DIR / "kustomization.yaml").write_text(services_kustomization)

    write_overlay_images(sorted(generated) + HAND_AUTHORED_IMAGES)
    instances = {svc["name"]: ("admin" if layer == 2 else "core") for section, layer in layer_map.items() for svc in catalog.get(section, [])}
    instances.pop("api-gateway", None)  # stateless, no database
    for ai_deployment in ("ai-orchestration-realtime", "ai-orchestration-batch"):
        instances.pop(ai_deployment, None)
    instances["ai-orchestration-svc"] = "core"  # one database shared by both AI tiers
    write_overlay_service_configs(instances)

    print(f"Generated {len(generated)} service manifests: {', '.join(generated)}")
    print(f"Skipped (hand-authored): {', '.join(skipped)}")


def env_prefix(service: str) -> str:
    """The settings prefix the service reads (pydantic env_prefix)."""
    import re

    config = (ROOT / "services" / service / "src" / "core" / "config.py").read_text()
    return re.search(r'env_prefix="([A-Z_]+)"', config).group(1)


def write_overlay_service_configs(instances: dict[str, str]) -> None:
    """Per environment, the <service>-config ConfigMap every deployment
    reads its Cloud SQL instance from (terraform/modules/cloudsql: Layer
    0/1 on voyqi-<env>-core-pg, Layer 2 on voyqi-<env>-admin-pg)."""
    import re

    for overlay in sorted(OVERLAYS_DIR.glob("*/kustomization.yaml")):
        env = overlay.parent.name
        project = re.search(rf"newName: {REGISTRY}/([^/]+)/", overlay.read_text()).group(1)
        kafka = f"voyqi-{env}-kafka-bootstrap.kafka.svc.cluster.local:9092"
        docs = [
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
            f"  name: {name}-config\n  namespace: voyqi\ndata:\n"
            f"  DB_INSTANCE_CONNECTION_NAME: \"{project}:us-east4:voyqi-{env}-{instance}-pg\"\n"
            f"  {env_prefix(name)}KAFKA_BOOTSTRAP_SERVERS: \"{kafka}\"\n"
            for name, instance in sorted(instances.items())
        ]
        header = "# Generated by scripts/generate_service_manifests.py — do not edit.\n"
        (overlay.parent / "service-config.yaml").write_text(header + "---\n".join(docs))
        text = overlay.read_text()
        if "service-config.yaml" not in text:
            text = text.replace("resources:\n  - ../../base\n", "resources:\n  - ../../base\n  - service-config.yaml\n", 1)
            overlay.write_text(text)
        print(f"{env}: {len(docs)} service ConfigMaps")
        write_workload_identity_patch(overlay, env, project)


# Kubernetes service account -> GCP service account (terraform/modules/iam:
# voyqi-<env>-<suffix>@<project>). Without the annotation, Workload
# Identity — and so the Cloud SQL proxy — can't authenticate.
WORKLOAD_IDENTITY = {"gateway-ksa": "gateway", "core-svc-ksa": "core-svc", "ai-orch-ksa": "ai-orch", "admin-svc-ksa": "admin-svc"}


def write_workload_identity_patch(overlay, env: str, project: str) -> None:
    docs = [
        "apiVersion: v1\nkind: ServiceAccount\nmetadata:\n"
        f"  name: {ksa}\n  namespace: voyqi\n  annotations:\n"
        f"    iam.gke.io/gcp-service-account: voyqi-{env}-{suffix}@{project}.iam.gserviceaccount.com\n"
        for ksa, suffix in WORKLOAD_IDENTITY.items()
    ]
    (overlay.parent / "workload-identity-patch.yaml").write_text(
        "# Generated by scripts/generate_service_manifests.py — do not edit.\n" + "---\n".join(docs)
    )
    text = overlay.read_text()
    if "workload-identity-patch.yaml" not in text:
        entry = "  - path: workload-identity-patch.yaml\n"
        text = text.replace("\npatches:\n", "\npatches:\n" + entry, 1) if "\npatches:\n" in text else text.rstrip("\n") + "\n\npatches:\n" + entry
        overlay.write_text(text)


def write_overlay_images(images: list[str]) -> None:
    """Rewrites each overlay's `images:` block so every image is pulled
    from that environment's project with its tag. Project and tag are
    read from the block's existing first entry."""
    import re

    for overlay in sorted(OVERLAYS_DIR.glob("*/kustomization.yaml")):
        text = overlay.read_text()
        block = re.search(r"^images:\n((?:[ #].*\n|\n)*?)(?=^\S|\Z)", text, re.M)
        if not block:
            continue
        project = re.search(rf"newName: {REGISTRY}/([^/]+)/", block.group(1)).group(1)
        tag = re.search(r"newTag: (\S+)", block.group(1)).group(1)
        entries = "".join(
            f"  - name: {REGISTRY}/PROJECT_ID/voyqi/{image}\n"
            f"    newName: {REGISTRY}/{project}/voyqi/{image}\n"
            f"    newTag: {tag}\n"
            for image in images
        )
        overlay.write_text(text[: block.start(1)] + entries + "\n" + text[block.end(1):].lstrip("\n"))
        print(f"{overlay.parent.name}: {len(images)} images -> {project}:{tag}")


if __name__ == "__main__":
    main()
