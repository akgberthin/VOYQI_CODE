#!/usr/bin/env python3
"""Logs in through the gateway as every seeded role (scripts/seed_demo.py)
and checks what each may and may not reach — the platform's roles
actually enforced end to end, not just recorded.

    python3 scripts/role_smoke_test.py
Exit code 0 only if every expectation holds.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

ALLOW, DENY = True, False

# role -> [(method, path, allowed)]
MATRIX = {
    "super_admin": [
        ("GET", "/api/v1/rbac/roles", ALLOW), ("GET", "/api/v1/hrm/employees", ALLOW),
        ("GET", "/api/v1/executive/dashboard/summary?metric_domain=revenue", ALLOW), ("GET", "/api/v1/org/cases", ALLOW),
    ],
    "fleet_manager": [
        ("GET", "/api/v1/fleet/vehicles", ALLOW), ("GET", "/api/v1/hub/hubs", ALLOW), ("GET", "/api/v1/ai/decision-log", ALLOW),
        ("GET", "/api/v1/hrm/employees", DENY), ("POST", "/api/v1/rbac/roles", DENY),
    ],
    "dealer_gm": [
        ("GET", "/api/v1/dms/rooftops", ALLOW), ("GET", "/api/v1/org/funding-sources", ALLOW), ("GET", "/api/v1/crm/conversations", ALLOW),
        ("GET", "/api/v1/hrm/employees", DENY), ("GET", "/api/v1/executive/rollups", DENY),
    ],
    "hr_manager": [
        ("GET", "/api/v1/hrm/employees", ALLOW), ("GET", "/api/v1/dms/rooftops", DENY), ("GET", "/api/v1/org/cases", DENY),
    ],
    "marketing_manager": [
        ("GET", "/api/v1/marketing/campaigns", ALLOW), ("GET", "/api/v1/crm/conversations", ALLOW), ("GET", "/api/v1/fleet/vehicles", DENY),
    ],
    "executive": [
        ("GET", "/api/v1/executive/dashboard/summary?metric_domain=revenue", ALLOW), ("GET", "/api/v1/capital-finance/funding-sources", ALLOW),
        ("POST", "/api/v1/executive/rollups", DENY), ("GET", "/api/v1/rbac/roles", DENY),
    ],
    "compliance_officer": [
        ("GET", "/api/v1/identity/break-glass", ALLOW), ("GET", "/api/v1/security-compliance/policies", ALLOW),
        ("GET", "/api/v1/rbac/roles", ALLOW), ("POST", "/api/v1/rbac/roles", DENY), ("GET", "/api/v1/hrm/employees", DENY),
    ],
    "member": [
        ("GET", "/api/v1/auth/me", ALLOW), ("GET", "/api/v1/subscription/plans", ALLOW), ("GET", "/api/v1/hub/hubs", ALLOW),
        ("GET", "/api/v1/rbac/roles", DENY), ("GET", "/api/v1/hrm/employees", DENY), ("GET", "/api/v1/org/funding-sources", DENY),
        ("POST", "/api/v1/originations/credit-decisions/00000000-0000-0000-0000-000000000000/decide", DENY),
    ],
}


def request(base: str, tenant: str, method: str, path: str, body=None, token: str | None = None):
    headers = {"Content-Type": "application/json", "X-Tenant-Id": tenant}
    if token:
        headers["Cookie"] = f"access_token={token}"
    data = json.dumps(body).encode() if body is not None else (b"{}" if method != "GET" else None)
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def login(base: str, tenant: str, email: str, password: str) -> str:
    status, headers, body = request(base, tenant, "POST", "/api/v1/auth/login", {"email": email, "password": password})
    if status != 200:
        raise RuntimeError(f"login {email} -> {status} {body[:120]!r}")
    for cookie in headers.get_all("Set-Cookie") or []:
        if cookie.startswith("access_token="):
            return cookie.split(";", 1)[0].split("=", 1)[1]
    raise RuntimeError(f"login {email}: no access token cookie")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway", default="http://localhost:8000")
    parser.add_argument("--tenant", default="voyqi-demo")
    parser.add_argument("--password", default="Voyqi-Demo-2026!")
    parser.add_argument("--email-domain", default="voyqi.example")
    args = parser.parse_args()

    failures = 0
    for role, checks in MATRIX.items():
        email = f"{role.replace('_', '.')}@{args.email_domain}"
        try:
            token = login(args.gateway, args.tenant, email, args.password)
        except RuntimeError as exc:
            print(f"FAIL {role}: {exc}")
            failures += 1
            continue
        for method, path, allowed in checks:
            status, _headers, _body = request(args.gateway, args.tenant, method, path, token=token)
            ok = (status not in (401, 403)) if allowed else status == 403
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {role:<19} {method:<5} {path:<62} {status} ({'allow' if allowed else 'deny'})")
    print(f"\n{'all role checks passed' if not failures else f'{failures} role check(s) failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
