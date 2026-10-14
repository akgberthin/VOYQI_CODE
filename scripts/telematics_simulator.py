#!/usr/bin/env python3
"""Telematics simulator — stands in for real vehicle devices until
hardware telematics (MQTT, per-VIN client certs) is connected.

Each simulated vehicle has a home hub city. Every tick it reports a GPS
position, battery/fuel level, odometer and occasional diagnostic codes
to fleet-svc's real ingest endpoint. Now and then a vehicle wanders
outside its hub's geofence (fleet-svc raises an alert and the Fleet
Intelligence Agent assesses it); very rarely a device reports a tamper
signal, which always goes to human security review — never to AI.

Standard library only, so it runs anywhere Python 3.10+ does:

    python3 scripts/telematics_simulator.py --tenant voyqi-demo --vehicles 12 --ticks 30

or inside the compose network:

    docker compose --profile simulator up telematics-simulator
"""
import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
import uuid

HUBS = {
    "austin": (30.2672, -97.7431),
    "dallas": (32.7767, -96.7970),
    "houston": (29.7604, -95.3698),
    "miami": (25.7617, -80.1918),
}
# Stable per-tenant hub ids, so repeated runs report against the same hubs.
HUB_IDS = {name: uuid.uuid5(uuid.NAMESPACE_URL, f"voyqi-sim-hub-{name}") for name in HUBS}
MODELS = [
    ("Tesla", "Model 3", "urban", True), ("Tesla", "Model Y", "executive", True),
    ("BMW", "i7", "luxury", True), ("Mercedes-Benz", "S-Class", "luxury_plus", False),
    ("Porsche", "Taycan", "performance", True), ("Toyota", "Camry", "urban", False),
]
DTC_CODES = ["P0420", "P0171", "P0300", "C1201", "B1318", "P0A80"]
MILES_PER_DEGREE = 69.0


class FleetClient:
    def __init__(self, base_url: str, tenant_id: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Content-Type": "application/json", "X-Tenant-Id": tenant_id}

    def request(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{method} {path} -> {exc.code}: {exc.read().decode()[:200]}") from exc


class SimVehicle:
    def __init__(self, vehicle: dict, hub: str, electric: bool, rng: random.Random):
        self.id = vehicle["id"]
        self.vin = vehicle["vin"]
        self.hub = hub
        self.hub_lat, self.hub_lon = HUBS[hub]
        self.lat = self.hub_lat + rng.uniform(-0.05, 0.05)
        self.lon = self.hub_lon + rng.uniform(-0.05, 0.05)
        self.energy = rng.uniform(55, 100)
        self.odometer = int(vehicle.get("odometer_miles") or rng.randint(500, 20000))
        self.electric = electric
        self.rng = rng

    def step(self, wander: bool) -> None:
        reach = 0.6 if wander else 0.02  # degrees: ~40 miles vs ~1.4 miles
        self.lat += self.rng.uniform(-reach, reach)
        self.lon += self.rng.uniform(-reach, reach)
        if not wander:  # drift back toward the hub
            self.lat += (self.hub_lat - self.lat) * 0.2
            self.lon += (self.hub_lon - self.lon) * 0.2
        miles = self.rng.randint(1, 12) + (40 if wander else 0)
        self.odometer += miles
        self.energy -= miles * (0.25 if self.electric else 0.15)
        if self.energy < 15:  # charged / refuelled at the hub
            self.energy = self.rng.uniform(85, 100)

    def miles_from_hub(self) -> float:
        dlat = (self.lat - self.hub_lat) * MILES_PER_DEGREE
        dlon = (self.lon - self.hub_lon) * MILES_PER_DEGREE * math.cos(math.radians(self.hub_lat))
        return math.hypot(dlat, dlon)


def ensure_vehicles(client: FleetClient, count: int, rng: random.Random) -> list[SimVehicle]:
    existing = client.request("GET", "/api/v1/fleet/vehicles") or []
    vehicles = []
    hubs = list(HUBS)
    for i, vehicle in enumerate(existing[:count]):
        make_model = next((m for m in MODELS if m[0] == vehicle["make"] and m[1] == vehicle["model"]), MODELS[0])
        vehicles.append(SimVehicle(vehicle, hubs[i % len(hubs)], make_model[3], rng))
    while len(vehicles) < count:
        make, model, vclass, electric = rng.choice(MODELS)
        po = client.request("POST", "/api/v1/fleet/purchase-orders", {
            "source": "telematics-simulator", "vehicle_description": f"{make} {model}", "cost_usd": rng.randint(38, 140) * 1000,
            "created_by": "telematics-simulator",
        })
        vin = "SIM" + uuid.uuid4().hex[:14].upper()
        vehicle = client.request("POST", f"/api/v1/fleet/purchase-orders/{po['id']}/receive", {
            "vin": vin, "make": make, "model": model, "year": rng.choice([2024, 2025, 2026]), "vehicle_class": vclass,
            "ownership_entity": "VOYQI Fleet LLC",
        })
        vehicles.append(SimVehicle(vehicle, hubs[len(vehicles) % len(hubs)], electric, rng))
    return vehicles


def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    client = FleetClient(args.fleet_url, args.tenant)
    vehicles = ensure_vehicles(client, args.vehicles, rng)
    print(f"simulating {len(vehicles)} vehicles for tenant {args.tenant} against {args.fleet_url}", flush=True)

    stats = {"readings": 0, "geofence_alerts": 0, "tamper_events": 0, "errors": 0}
    tick = 0
    while args.ticks == 0 or tick < args.ticks:
        tick += 1
        for v in vehicles:
            wander = rng.random() < args.wander_rate
            v.step(wander)
            dtcs = [rng.choice(DTC_CODES)] if rng.random() < args.dtc_rate else []
            try:
                client.request("POST", "/api/v1/fleet/telemetry", {
                    "vehicle_id": v.id, "latitude": round(v.lat, 6), "longitude": round(v.lon, 6),
                    "battery_or_fuel_pct": round(v.energy, 1), "odometer_miles": v.odometer, "dtc_codes": dtcs,
                })
                stats["readings"] += 1
                if v.miles_from_hub() > args.geofence_miles:
                    result = client.request("POST", "/api/v1/fleet/geofence-check", {
                        "vehicle_id": v.id, "hub_id": str(HUB_IDS[v.hub]), "vehicle_lat": v.lat, "vehicle_lon": v.lon,
                        "hub_lat": v.hub_lat, "hub_lon": v.hub_lon,
                    })
                    if result and result.get("alert_created"):
                        stats["geofence_alerts"] += 1
                        print(f"  tick {tick}: {v.vin} {v.miles_from_hub():.0f} mi from {v.hub} hub -> geofence alert", flush=True)
                if rng.random() < args.tamper_rate:
                    client.request("POST", "/api/v1/fleet/tamper-events", {"vehicle_id": v.id, "detected_signal": rng.choice(["obd_unplugged", "gps_jamming", "can_bus_injection"])})
                    stats["tamper_events"] += 1
                    print(f"  tick {tick}: {v.vin} tamper signal -> human security review", flush=True)
            except (RuntimeError, urllib.error.URLError) as exc:
                stats["errors"] += 1
                print(f"  tick {tick}: {v.vin} error: {exc}", file=sys.stderr, flush=True)
        if args.ticks == 0 or tick < args.ticks:
            time.sleep(args.interval)

    print(f"done: {stats}", flush=True)
    return 1 if stats["errors"] and not stats["readings"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fleet-url", default="http://localhost:8004", help="fleet-svc base URL")
    parser.add_argument("--tenant", default="voyqi-demo")
    parser.add_argument("--vehicles", type=int, default=8, help="vehicles to simulate (existing ones are reused first)")
    parser.add_argument("--ticks", type=int, default=20, help="reporting rounds; 0 runs until stopped")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between rounds")
    parser.add_argument("--geofence-miles", type=float, default=25.0)
    parser.add_argument("--wander-rate", type=float, default=0.05, help="chance per reading of leaving the hub area")
    parser.add_argument("--dtc-rate", type=float, default=0.03, help="chance per reading of a diagnostic code")
    parser.add_argument("--tamper-rate", type=float, default=0.002, help="chance per reading of a tamper signal")
    parser.add_argument("--seed", type=int, default=None)
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
