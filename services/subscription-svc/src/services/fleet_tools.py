"""
Real HTTP calls into fleet-svc — used by swap_engine and continuity_engine
to check class-eligible vehicle availability and propose transfers, the
same fleet-svc staging surface the Fleet Intelligence Agent itself uses.
"""
from typing import Any

import httpx

from src.core.config import get_settings


def search_available_vehicles(tenant_id: str, vehicle_class: str, hub_id: str | None = None) -> list[dict[str, Any]]:
    settings = get_settings()
    params: dict[str, str] = {"vehicle_class": vehicle_class, "status": "staged"}
    if hub_id:
        params["hub_id"] = hub_id
    with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
        resp = client.get("/api/v1/fleet/vehicles", params=params, headers={"X-Tenant-Id": tenant_id})
        resp.raise_for_status()
        return resp.json()


def propose_transfer(tenant_id: str, vehicle_id: str, to_hub_id: str, reason: str) -> dict[str, Any]:
    settings = get_settings()
    with httpx.Client(base_url=settings.fleet_svc_base_url, timeout=10.0) as client:
        resp = client.post(
            "/api/v1/fleet/transfer-orders",
            json={"vehicle_id": vehicle_id, "to_hub_id": to_hub_id, "reason": reason},
            headers={"X-Tenant-Id": tenant_id},
        )
        resp.raise_for_status()
        return resp.json()
