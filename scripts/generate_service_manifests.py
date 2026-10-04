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

    print(f"Generated {len(generated)} service manifests: {', '.join(generated)}")
    print(f"Skipped (hand-authored): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
