"""
CLI entrypoint for scheduled batch jobs. Invoked directly by a
Kubernetes CronJob (see infra/k8s/base/ai-orchestration-churn-sweep-
cronjob.yaml) — not part of the FastAPI app's own process, since a
CronJob's whole point is running to completion and exiting, not
staying up like the API or the two Kafka consumer tiers.

Usage: python -m src.batch.run_sweep churn-risk --tenant-id <tenant>
"""
import argparse
import asyncio
import logging
import sys

from src.core.database import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ai-orchestration-svc.batch.run_sweep")

SWEEPS = {
    "churn-risk": "src.batch.churn_sweep:run_churn_sweep",
}


async def _run(sweep_name: str, tenant_id: str) -> int:
    if sweep_name not in SWEEPS:
        logger.error("Unknown sweep '%s' — available: %s", sweep_name, ", ".join(SWEEPS))
        return 2

    module_path, func_name = SWEEPS[sweep_name].split(":")
    module = __import__(module_path, fromlist=[func_name])
    sweep_fn = getattr(module, func_name)

    async with AsyncSessionLocal() as db:
        summary = await sweep_fn(db, tenant_id)

    logger.info("Sweep '%s' finished: %s", sweep_name, summary)
    return 1 if summary.get("aborted") else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a scheduled AI orchestration batch sweep.")
    parser.add_argument("sweep", choices=list(SWEEPS.keys()))
    parser.add_argument("--tenant-id", required=True)
    args = parser.parse_args()

    exit_code = asyncio.run(_run(args.sweep, args.tenant_id))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
