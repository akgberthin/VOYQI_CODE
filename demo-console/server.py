#!/usr/bin/env python3
"""VOYQI demo console — a browser front end for showing the running
platform before the React apps exist.

    python3 scripts/seed_demo.py          # once
    python3 demo-console/server.py        # then open http://localhost:8090

Serves the page and forwards /api/* to the API gateway. The page names
the demo role it is acting as (X-Demo-Role: fleet.manager, member, …);
this server signs in as that seeded user and attaches the session
cookie, so every call goes through the gateway's real authentication
and authorization. A request without X-Demo-Role goes out anonymous,
like the public landing-page form.

It also starts the telematics simulator (/demo/simulator) and runs the
logout check (/demo/logout-check), which need more than one request.

Standard library only. For demos — not a production front end.
"""
import argparse
import collections
import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PASSWORD = "Voyqi-Demo-2026!"
DOMAIN = "voyqi.example"
ROLES = {"super.admin", "fleet.manager", "dealer.gm", "hr.manager", "marketing.manager", "executive",
         "compliance.officer", "member", "member.two"}


class Gateway:
    def __init__(self, base: str, tenant: str):
        self.base, self.tenant = base.rstrip("/"), tenant
        self.tokens: dict[str, str] = {}
        self.lock = threading.Lock()

    def send(self, method: str, path: str, body: bytes | None, token: str | None = None):
        headers = {"Content-Type": "application/json", "X-Tenant-Id": self.tenant}
        if token:
            headers["Cookie"] = f"access_token={token}"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers
        except urllib.error.URLError as exc:
            return 502, json.dumps({"detail": f"gateway unreachable: {exc.reason}"}).encode(), {}

    def login(self, role: str) -> str:
        status, body, headers = self.send("POST", "/api/v1/auth/login",
                                          json.dumps({"email": f"{role}@{DOMAIN}", "password": PASSWORD}).encode())
        if status != 200:
            raise RuntimeError(f"login as {role} failed ({status}) — run scripts/seed_demo.py")
        for header in headers.get_all("Set-Cookie") or []:
            if header.startswith("access_token="):
                return header.split(";", 1)[0].split("=", 1)[1]
        raise RuntimeError(f"login as {role}: no session cookie")

    def token(self, role: str, fresh: bool = False) -> str:
        with self.lock:
            if fresh or role not in self.tokens:
                self.tokens[role] = self.login(role)
            return self.tokens[role]

    def call(self, method: str, path: str, body: bytes | None, role: str | None):
        if not role:
            return self.send(method, path, body)
        status, data, headers = self.send(method, path, body, self.token(role))
        if status == 401:  # expired or revoked: sign in again once
            status, data, headers = self.send(method, path, body, self.token(role, fresh=True))
        return status, data, headers


class Simulator:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.lines: collections.deque[str] = collections.deque(maxlen=200)

    def start(self, tenant: str, fleet_url: str, mqtt_host: str, vehicles: int, ticks: int) -> bool:
        if self.running():
            return False
        self.lines.clear()
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "scripts" / "telematics_simulator.py"), "--tenant", tenant,
             "--fleet-url", fleet_url, "--vehicles", str(vehicles), "--ticks", str(ticks), "--interval", "2",
             "--wander-rate", "0.07", "--tamper-rate", "0.006", "--transport", "mqtt", "--mqtt-host", mqtt_host],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        return True

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.append(line.rstrip())

    def stop(self) -> None:
        if self.running():
            self.proc.terminate()

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def make_handler(gateway: Gateway, simulator: Simulator, args):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(HERE / "static"), **kw)

        def log_message(self, fmt, *a):  # quiet: only errors
            if "/api/" not in self.path and "/demo/" not in self.path:
                return
            if a and str(a[1]).startswith(("4", "5")) and str(a[1]) not in ("401", "403", "404", "409", "422"):
                super().log_message(fmt, *a)

        def _json(self, status: int, payload) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes | None:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else None

        def _proxy(self, method: str) -> None:
            role = self.headers.get("X-Demo-Role") or None
            if role and role not in ROLES:
                return self._json(400, {"detail": f"unknown demo role {role}"})
            try:
                status, data, _ = gateway.call(method, self.path, self._body() if method != "GET" else None, role)
            except RuntimeError as exc:
                return self._json(503, {"detail": str(exc)})
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/api/"):
                return self._proxy("GET")
            if self.path.startswith("/demo/simulator"):
                return self._json(200, {"running": simulator.running(), "lines": list(simulator.lines)[-40:]})
            if self.path == "/demo/config":
                return self._json(200, {"tenant": gateway.tenant, "gateway": gateway.base})
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith("/api/"):
                return self._proxy("POST")
            if self.path == "/demo/simulator/start":
                opts = json.loads(self._body() or b"{}")
                started = simulator.start(gateway.tenant, args.fleet_url, args.mqtt_host, int(opts.get("vehicles", 12)), int(opts.get("ticks", 60)))
                return self._json(200 if started else 409, {"running": simulator.running()})
            if self.path == "/demo/simulator/stop":
                simulator.stop()
                return self._json(200, {"running": False})
            if self.path == "/demo/logout-check":
                return self._json(200, logout_check(gateway, json.loads(self._body() or b"{}").get("role", "marketing.manager")))
            return self._json(404, {"detail": "not found"})

        def do_PUT(self):
            return self._proxy("PUT") if self.path.startswith("/api/") else self._json(404, {})

        def do_DELETE(self):
            return self._proxy("DELETE") if self.path.startswith("/api/") else self._json(404, {})

    return Handler


def logout_check(gateway: Gateway, role: str) -> dict:
    """Sign in, use the session, sign out, try the same session again."""
    token = gateway.login(role)
    path = "/api/v1/auth/me"
    before, _, _ = gateway.send("GET", path, None, token)
    out, _, _ = gateway.send("POST", "/api/v1/auth/logout", None, token)
    after, _, _ = gateway.send("GET", path, None, token)
    return {"role": role, "before_logout": before, "logout": out, "after_logout": after}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--gateway", default="http://localhost:8000")
    parser.add_argument("--tenant", default="voyqi-demo")
    parser.add_argument("--fleet-url", default="http://localhost:8004", help="fleet-svc, for the simulator's vehicle setup")
    parser.add_argument("--mqtt-host", default="localhost")
    args = parser.parse_args()
    gateway = Gateway(args.gateway, args.tenant)
    simulator = Simulator()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(gateway, simulator, args))
    print(f"VOYQI demo console: http://localhost:{args.port}  (gateway {args.gateway}, tenant {args.tenant})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        simulator.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
