#!/usr/bin/env python3
"""Telematics simulator — stands in for real vehicle devices until
hardware telematics units are installed.

Each simulated vehicle has a home hub city. Every tick it reports a GPS
position, battery/fuel level, odometer and occasional diagnostic codes.
Now and then a vehicle wanders outside its hub's geofence (fleet-svc
raises an alert and the Fleet Intelligence Agent assesses it); very
rarely a device reports a tamper signal, which always goes to human
security review — never to AI.

Two transports:

- http (default): posts to fleet-svc's ingest endpoints, and asks for
  the geofence check itself.
- mqtt: publishes like a real device to voyqi/{tenant}/vehicles/{vin}/
  telemetry|tamper, one connection per VIN; fleet-svc geofences
  server-side against each vehicle's hub. With --mqtt-cert-dir each
  vehicle connects over TLS with <dir>/<VIN>.crt/.key (VIN as the
  certificate Common Name), as production devices do.

Standard library only, so it runs anywhere Python 3.10+ does:

    python3 scripts/telematics_simulator.py --tenant voyqi-demo --vehicles 12 --ticks 30
    python3 scripts/telematics_simulator.py --transport mqtt --mqtt-host localhost --ticks 30

or inside the compose network:

    docker compose --profile simulator up telematics-simulator
"""
import argparse
import json
import math
import random
import socket
import ssl
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


def _mqtt_str(value: str) -> bytes:
    raw = value.encode()
    return len(raw).to_bytes(2, "big") + raw


def _mqtt_length(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


class MqttPublisher:
    """Just enough MQTT 3.1.1 to connect and publish QoS-0 messages."""

    def __init__(self, host: str, port: int, client_id: str, keepalive: int, username: str = "", password: str = "",
                 tls: ssl.SSLContext | None = None):
        sock = socket.create_connection((host, port), timeout=10)
        self.sock = tls.wrap_socket(sock, server_hostname=host) if tls else sock
        flags, payload = 0x02, _mqtt_str(client_id)  # clean session
        if username:
            flags, payload = flags | 0x80, payload + _mqtt_str(username)
        if password:
            flags, payload = flags | 0x40, payload + _mqtt_str(password)
        self._send(0x10, _mqtt_str("MQTT") + bytes([4, flags]) + keepalive.to_bytes(2, "big") + payload)
        connack = self._recv(4)
        if connack[0] != 0x20 or connack[3] != 0:
            raise RuntimeError(f"MQTT connect refused for {client_id} (return code {connack[3]})")

    def publish(self, topic: str, body: dict) -> None:
        self._send(0x30, _mqtt_str(topic) + json.dumps(body).encode())

    def close(self) -> None:
        try:
            self._send(0xE0, b"")
        finally:
            self.sock.close()

    def _send(self, kind: int, body: bytes) -> None:
        self.sock.sendall(bytes([kind]) + _mqtt_length(len(body)) + body)

    def _recv(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise RuntimeError("MQTT broker closed the connection")
            data += chunk
        return data


class SimVehicle:
    def __init__(self, vehicle: dict, hub: str, electric: bool, rng: random.Random):
        self.id = vehicle["id"]
        self.vin = vehicle["vin"]
        self.current_hub_id = vehicle.get("current_hub_id")
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


def ensure_located_hubs(client: FleetClient, vehicles: list[SimVehicle], radius_miles: float) -> None:
    """Server-side geofencing needs each vehicle stationed at a hub with
    coordinates: create the simulator's hubs if missing and move hub-less
    vehicles to their home hub (propose → dispatch → complete)."""
    existing = {h["name"]: h for h in client.request("GET", "/api/v1/fleet/hubs") or []}
    hub_ids = {}
    for name, (lat, lon) in HUBS.items():
        label = f"Sim {name.title()}"
        hub = existing.get(label) or client.request("POST", "/api/v1/fleet/hubs", {
            "name": label, "region": name, "latitude": lat, "longitude": lon, "geofence_radius_miles": radius_miles,
        })
        hub_ids[name] = hub["id"]
    by_id = {hub_id: name for name, hub_id in hub_ids.items()}
    for v in vehicles:
        if v.current_hub_id in by_id:  # already at a simulator hub: report around it
            v.hub = by_id[v.current_hub_id]
            v.hub_lat, v.hub_lon = HUBS[v.hub]
            v.lat, v.lon = v.hub_lat, v.hub_lon
        elif v.current_hub_id is None:
            order = client.request("POST", "/api/v1/fleet/transfer-orders", {
                "vehicle_id": v.id, "to_hub_id": hub_ids[v.hub], "reason": "telematics simulator home hub",
            })
            client.request("POST", f"/api/v1/fleet/transfer-orders/{order['id']}/dispatch", {"dispatched_by": "telematics-simulator", "driver_id": "sim"})
            client.request("POST", f"/api/v1/fleet/transfer-orders/{order['id']}/complete")
            v.current_hub_id = hub_ids[v.hub]


def mqtt_tls(cert_dir: str | None, ca_file: str | None, vin: str) -> ssl.SSLContext | None:
    if not cert_dir:
        return None
    context = ssl.create_default_context(cafile=ca_file)
    context.load_cert_chain(f"{cert_dir}/{vin}.crt", f"{cert_dir}/{vin}.key")
    return context


def run_mqtt(args: argparse.Namespace, client: FleetClient, vehicles: list[SimVehicle], rng: random.Random) -> int:
    ensure_located_hubs(client, vehicles, args.geofence_miles)
    keepalive = max(60, int(args.interval * 3))
    devices = {
        v.vin: MqttPublisher(args.mqtt_host, args.mqtt_port, v.vin, keepalive, args.mqtt_username, args.mqtt_password,
                             mqtt_tls(args.mqtt_cert_dir, args.mqtt_ca_file, v.vin))
        for v in vehicles
    }
    print(f"{len(devices)} devices connected to mqtt://{args.mqtt_host}:{args.mqtt_port}", flush=True)
    stats = {"readings": 0, "left_geofence": 0, "tamper_events": 0, "errors": 0}
    tick = 0
    try:
        while args.ticks == 0 or tick < args.ticks:
            tick += 1
            for v in vehicles:
                was_outside = v.miles_from_hub() > args.geofence_miles
                v.step(rng.random() < args.wander_rate)
                dtcs = [rng.choice(DTC_CODES)] if rng.random() < args.dtc_rate else []
                topic = f"voyqi/{args.tenant}/vehicles/{v.vin}"
                try:
                    devices[v.vin].publish(f"{topic}/telemetry", {
                        "latitude": round(v.lat, 6), "longitude": round(v.lon, 6),
                        "battery_or_fuel_pct": round(max(v.energy, 0), 1), "odometer_miles": v.odometer, "dtc_codes": dtcs,
                    })
                    stats["readings"] += 1
                    if v.miles_from_hub() > args.geofence_miles and not was_outside:
                        stats["left_geofence"] += 1
                        print(f"  tick {tick}: {v.vin} left the {v.hub} hub area ({v.miles_from_hub():.0f} mi)", flush=True)
                    if rng.random() < args.tamper_rate:
                        devices[v.vin].publish(f"{topic}/tamper", {"detected_signal": rng.choice(["obd_unplugged", "gps_jamming", "can_bus_injection"])})
                        stats["tamper_events"] += 1
                        print(f"  tick {tick}: {v.vin} tamper signal -> human security review", flush=True)
                except OSError as exc:
                    stats["errors"] += 1
                    print(f"  tick {tick}: {v.vin} error: {exc}", file=sys.stderr, flush=True)
            if args.ticks == 0 or tick < args.ticks:
                time.sleep(args.interval)
    finally:
        for device in devices.values():
            device.close()
    print(f"done: {stats}", flush=True)
    return 1 if stats["errors"] and not stats["readings"] else 0


def run(args: argparse.Namespace) -> int:
    rng = random.Random(args.seed)
    client = FleetClient(args.fleet_url, args.tenant)
    vehicles = ensure_vehicles(client, args.vehicles, rng)
    print(f"simulating {len(vehicles)} vehicles for tenant {args.tenant} over {args.transport}", flush=True)
    if args.transport == "mqtt":
        return run_mqtt(args, client, vehicles, rng)

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
    parser.add_argument("--fleet-url", default="http://localhost:8004", help="fleet-svc base URL (vehicle/hub setup, and ingest over http)")
    parser.add_argument("--transport", choices=["http", "mqtt"], default="http")
    parser.add_argument("--mqtt-host", default="localhost")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-username", default="")
    parser.add_argument("--mqtt-password", default="")
    parser.add_argument("--mqtt-cert-dir", default=None, help="per-VIN client certs <VIN>.crt/.key (TLS)")
    parser.add_argument("--mqtt-ca-file", default=None, help="CA that signed the broker certificate")
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
