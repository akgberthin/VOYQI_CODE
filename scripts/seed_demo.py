#!/usr/bin/env python3
"""Seed a demo tenant: one login per platform role, the role permissions
the gateway enforces, and sample business data across the services.

All sample data is invented for demonstration — replace it with the
client's real business data before any external demo.

Users register through the gateway's public endpoint exactly like a real
sign-up. Roles and sample data are written straight to the services on
the internal network (the bootstrap a platform operator would do — no
user can grant themselves a role through the gateway). Safe to re-run:
existing roles, users and records are reused, not duplicated.

    python3 scripts/seed_demo.py                 # tenant "voyqi-demo"
    python3 scripts/seed_demo.py --tenant acme --password 'Another-Pass-123'
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

LOCAL = {
    "gateway": 8000, "subscription": 8003, "fleet": 8004, "rental": 8005, "hub": 8006, "marketing": 8007, "dms": 8008,
    "pricing": 8010, "rbac": 8011, "crm": 8021, "hrm": 8023, "executive": 8032, "originations": 8036,
}

# role key -> (display name, user_type, permissions). Module names are the
# gateway route prefixes; "<module>.*" grants read and write.
ROLES = {
    "super_admin": ("Super Admin", "admin", ["*"]),
    "fleet_manager": ("Fleet Manager", "employee", ["fleet.*", "hub.*", "rental.read", "ai.*"]),
    "dealer_gm": ("Dealer General Manager", "dealer", ["dms.*", "sales.*", "originations.*", "org.*", "crm.*", "pricing.read", "ai.*"]),
    "hr_manager": ("HR Manager", "employee", ["hrm.*", "user.read", "notifications.write", "ai.*"]),
    # AI drafting goes through marketing-svc, so marketing needs no AI admin rights.
    "marketing_manager": ("Marketing Manager", "employee", ["marketing.*", "crm.*", "ai.read"]),
    "executive": ("Executive", "employee", [
        "executive.read", "management.read", "real-estate.read", "capital-finance.read", "insurance-risk.read", "ai.read",
    ]),
    "compliance_officer": ("Compliance Officer", "employee", ["security-compliance.*", "identity.*", "rbac.read", "ai.*", "insurance-risk.*"]),
    # ".self": only the member self-service endpoints, on their own records
    # (enforced by the gateway). Rental reservations aren't linked to a
    # user, so members have no rental access.
    "member": ("Member", "member", ["subscription.self", "payment.self", "hub.read", "pricing.read"]),
}
# Extra logins that share a role, e.g. a second member to show members
# can't see each other's records.
EXTRA_LOGINS = {"member.two": "member"}


class Api:
    def __init__(self, host: str, tenant: str):
        self.host, self.tenant = host, tenant

    def call(self, service: str, method: str, path: str, body=None, ok=(200, 201), cookie: str | None = None):
        url = f"http://{self.host}:{LOCAL[service]}{path}"
        headers = {"Content-Type": "application/json", "X-Tenant-Id": self.tenant}
        if cookie:
            headers["Cookie"] = f"access_token={cookie}"
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None), resp.headers
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:300]
            if exc.code in ok:
                return exc.code, None, exc.headers
            raise RuntimeError(f"{method} {service}{path} -> {exc.code}: {detail}") from exc


def login(api: Api, email: str, password: str) -> str:
    _status, _body, headers = api.call("gateway", "POST", "/api/v1/auth/login", {"email": email, "password": password})
    for cookie in headers.get_all("Set-Cookie") or []:
        if cookie.startswith("access_token="):
            return cookie.split(";", 1)[0].split("=", 1)[1]
    raise RuntimeError(f"login for {email} returned no access token")


def seed_users_and_roles(api: Api, password: str, domain: str) -> dict:
    existing_roles = {r["key"]: r for r in api.call("rbac", "GET", "/api/v1/rbac/roles")[1]}
    existing_platform_users = {u["id"] for u in api.call("rbac", "GET", "/api/v1/rbac/users")[1]}
    roles = {}
    for key, (name, _user_type, permissions) in ROLES.items():
        wanted_exact = sorted(p for p in permissions if not p.endswith("*"))
        wanted_wild = sorted(p for p in permissions if p.endswith("*"))
        role = existing_roles.get(key)
        if role is None:
            role = api.call("rbac", "POST", "/api/v1/rbac/roles", {
                "key": key, "name": name, "permission_keys": wanted_exact, "wildcard_permissions": wanted_wild,
                "is_system_role": key == "super_admin",
            })[1]
        elif sorted(role["permission_keys"]) != wanted_exact or sorted(role["wildcard_permissions"]) != wanted_wild:
            print(f"  ! role {key} exists with different permissions than this script defines — left unchanged", flush=True)
        roles[key] = role

    logins = [(key.replace("_", "."), key) for key in ROLES] + list(EXTRA_LOGINS.items())
    accounts = {}
    for login_name, role_key in logins:
        name, user_type, _permissions = ROLES[role_key]
        email = f"{login_name}@{domain}"
        first, _, last = name.partition(" ")
        try:
            api.call("gateway", "POST", "/api/v1/user/users/register", {
                "user_type": user_type, "first_name": first, "last_name": last or login_name.split(".")[-1].title(),
                "email": email, "password": password,
            })
        except RuntimeError as exc:
            if "409" not in str(exc):
                raise
        token = login(api, email, password)
        user_id = api.call("gateway", "GET", "/api/v1/auth/me", cookie=token)[1]["user_id"]
        if user_id not in existing_platform_users:
            api.call("rbac", "POST", "/api/v1/rbac/users", {"id": user_id, "email": email, "display_name": name})
            api.call("rbac", "POST", "/api/v1/rbac/role-assignments", {"user_id": user_id, "role_id": roles[role_key]["id"], "assigned_by": "seed_demo"})
        accounts[login_name] = {"email": email, "user_id": user_id, "role": role_key}
        print(f"  {role_key:<19} {email}", flush=True)
    return accounts


def seed_business_data(api: Api, accounts: dict) -> None:
    def ensure(service, list_path, create_path, items, match_key):
        current = api.call(service, "GET", list_path)[1] or []
        have = {str(item.get(match_key)) for item in current}
        created = 0
        for item in items:
            if str(item[match_key]) not in have:
                api.call(service, "POST", create_path, item)
                created += 1
        print(f"  {service:<13} {create_path:<40} +{created} (had {len(have)})", flush=True)

    ensure("subscription", "/api/v1/subscription/plans", "/api/v1/subscription/plans", [
        {"mobility_class": c, "market": "national", "monthly_price_usd": p} for c, p in
        [("urban", 449), ("family", 699), ("executive", 899), ("luxury", 1499), ("performance", 1799)]
    ], "mobility_class")
    members = 0
    for account in accounts.values():
        if account["role"] != "member":
            continue
        status, _body, _h = api.call("subscription", "GET", f"/api/v1/subscription/members/by-user-id/{account['user_id']}", ok=(200, 404))
        if status == 404:
            api.call("subscription", "POST", "/api/v1/subscription/members", {
                "user_id": account["user_id"], "full_name": account["email"].split("@")[0].replace(".", " ").title(), "email": account["email"],
            })
            members += 1
    print(f"  {'subscription':<13} {'member profiles for member logins':<40} +{members}", flush=True)

    created = 0
    for mobility_class, price_cents in (("urban", 44900), ("executive", 89900), ("luxury", 149900)):
        status, _body, _h = api.call("pricing", "GET", f"/api/v1/pricing/rate-cards/current?mobility_class={mobility_class}", ok=(200, 404))
        if status == 404:
            api.call("pricing", "POST", "/api/v1/pricing/rate-cards", {"mobility_class": mobility_class, "price_cents": price_cents, "changed_by": "seed_demo"})
            created += 1
    print(f"  {'pricing':<13} {'/api/v1/pricing/rate-cards':<40} +{created}", flush=True)
    ensure("hub", "/api/v1/hub/hubs", "/api/v1/hub/hubs", [
        {"name": "Austin Downtown", "hub_type": "city_business", "latitude": 30.2672, "longitude": -97.7431, "capacity": 60, "has_lounge": True},
        {"name": "DFW Airport", "hub_type": "airport", "latitude": 32.8998, "longitude": -97.0403, "capacity": 120, "has_lounge": True},
        {"name": "Houston Galleria", "hub_type": "city_business", "latitude": 29.7376, "longitude": -95.4616, "capacity": 45},
        {"name": "Miami Brickell", "hub_type": "residential", "latitude": 25.7617, "longitude": -80.1918, "capacity": 40},
    ], "name")
    ensure("dms", "/api/v1/dms/rooftops", "/api/v1/dms/rooftops", [
        {"name": "VOYQI Toyota Austin", "oem_brand": "Toyota", "license_number": "TX-D-10442", "license_expiry": str(date.today() + timedelta(days=300)), "holding_company": "VOYQI Texas Holdings"},
        {"name": "VOYQI BMW Dallas", "oem_brand": "BMW", "license_number": "TX-D-20817", "license_expiry": str(date.today() + timedelta(days=25)), "holding_company": "VOYQI Texas Holdings"},
    ], "name")
    ensure("dms", "/api/v1/dms/lending/lenders", "/api/v1/dms/lending/lenders", [{"name": n} for n in ("Lone Star Credit Union", "Capital Auto Finance", "VOYQI Captive Finance")], "name")
    ensure("originations", "/api/v1/org/funding-sources", "/api/v1/org/funding-sources", [
        {"name": "VOYQI Captive Finance", "applicable_origination_type": "subscription_enrollment", "min_risk_score": 0.4, "max_amount_usd": 60000, "reserve_participation_pct": 2.5},
        {"name": "Lone Star Credit Union", "applicable_origination_type": "subscription_enrollment", "min_risk_score": 0.6, "max_amount_usd": 40000, "reserve_participation_pct": 1.5},
        {"name": "Rental Conversion Bank", "applicable_origination_type": "rental_conversion", "min_risk_score": 0.5, "max_amount_usd": 25000, "reserve_participation_pct": 1.0},
    ], "name")
    ensure("marketing", "/api/v1/marketing/campaigns", "/api/v1/marketing/campaigns", [
        {"name": "Portable Mobility Launch — Texas", "channel": "email", "objective": "acquisition", "created_by": "seed_demo"},
        {"name": "Winter Upgrade Offer", "channel": "sms", "objective": "upsell", "created_by": "seed_demo"},
    ], "name")
    ensure("hrm", "/api/v1/hrm/employees", "/api/v1/hrm/employees", [
        {"full_name": n, "job_title": t, "department": d, "email": f"{n.split()[0].lower()}@voyqi.example", "annual_salary_usd": s}
        for n, t, d, s in [
            ("Priya Raman", "Fleet Operations Lead", "fleet", 98000), ("Marcus Hill", "Service Advisor", "dealership", 61000),
            ("Elena Torres", "HR Business Partner", "people", 88000), ("Jordan Lee", "Member Concierge", "member_experience", 54000),
        ]
    ], "full_name")
    period_end = date.today().replace(day=1) - timedelta(days=1)
    period_start = period_end.replace(day=1)
    rollups = api.call("executive", "GET", "/api/v1/executive/rollups")[1] or []
    if not rollups:
        for subsidiary, revenue in (("voyqi-mobility", 1_840_000), ("voyqi-rental", 1_120_000), ("voyqi-dealer-group", 4_350_000)):
            api.call("executive", "POST", "/api/v1/executive/rollups", {
                "subsidiary_key": subsidiary, "metric_domain": "revenue", "period_start": str(period_start), "period_end": str(period_end),
                "metrics": {"revenue_usd": revenue, "gross_margin_pct": 21.5}, "source_service": "seed_demo",
            })
    print(f"  executive     revenue rollups for {period_start:%B %Y} ({'existing' if rollups else 'created'})", flush=True)


def seed_marketing(api: Api) -> None:
    """A lived-in marketing workspace: segments, templates, campaigns with
    content (one held by the guardrail), delivered sends with results, a
    scheduled send, suppression entries, a social post, a concluded A/B
    test and some leads. Runs once (skipped when segments exist)."""
    M = "/api/v1/marketing"
    if api.call("marketing", "GET", f"{M}/audience/segments")[1]:
        print("  marketing     demo workspace already seeded", flush=True)
        return
    post = lambda path, body: api.call("marketing", "POST", f"{M}{path}", body)[1]
    seg = {name: post("/audience/segments", {"name": name, "filter_criteria": crit, "created_by": "seed_demo"}) for name, crit in (
        ("Austin renters, EV-curious", {"city": "Austin", "last_rental_days": "90", "vehicle_class": "urban"}),
        ("Executive members", {"member_tier": "executive", "subscription_status": "active"}),
        ("Lapsed subscribers", {"subscription_status": "cancelled", "channel_opt_in": "sms"}),
    )}
    for (name, _), count in zip(seg.items(), (4820, 1260, 930)):
        api.call("marketing", "PATCH", f"{M}/audience/segments/{seg[name]['id']}/member-count", {"member_count": count})
    for title, channel, body, tags in (
        ("Welcome to VOYQI", "email", "Welcome to VOYQI, {first_name}\n\nYour first month is ready. Pick up at any hub — keys are in the app.", ["welcome"]),
        ("EV subscription intro", "email", "Drive electric, month to month\n\nOne price covers the car, insurance and charging. Swap classes any time.", ["ev", "acquisition"]),
        ("Pickup reminder", "sms", "VOYQI: your car is ready at {hub} from {time}. Reply STOP to opt out.", ["service"]),
        ("Weekend luxury", "push", "This weekend: S-Class and Taycan available at your hub. Tap to reserve.", ["luxury"]),
    ):
        post("/library", {"title": title, "channel": channel, "body_text": body, "tags": tags, "created_by": "seed_demo"})
    for contact, channel, reason in (("optout@example.com", "email", "opt_out"), ("+15125550199", "sms", "complaint"), ("bounce@example.com", "email", "bounce")):
        post("/suppression", {"channel": channel, "contact_identifier": contact, "reason": reason})

    def campaign(name, channel, objective, segment=None, status="active"):
        c = post("/campaigns", {"name": name, "channel": channel, "objective": objective, "created_by": "marketing.manager@voyqi.example", "audience_segment_id": seg[segment]["id"] if segment else None})
        if status != "draft":
            api.call("marketing", "PATCH", f"{M}/campaigns/{c['id']}/status", {"status": status})
        return c

    def draft(c, body, subject=""):
        return post("/content", {"campaign_id": c["id"], "channel": c["channel"], "subject_line": subject, "body_text": body, "created_by": "marketing.manager@voyqi.example"})

    now = datetime.now(timezone.utc)
    ev = campaign("Spring EV subscription push", "email", "Convert Austin renters to monthly EV subscriptions", "Austin renters, EV-curious")
    a = draft(ev, "Your next car can be electric — with insurance and charging included, from $449/month. Swap any month.", "Go electric, month to month")
    b = draft(ev, "Rented with us lately? Keep the keys: an EV subscription with everything included, from $449/month.", "Keep the keys this time")
    draft(ev, "Guaranteed lowest price in Texas — risk-free, cancel any time!", "Guaranteed savings")  # held by the guardrail
    send = post("/sends", {"campaign_id": ev["id"], "content_draft_id": a["id"], "channel": "email", "scheduled_at": (now - timedelta(days=3)).isoformat()})
    post(f"/sends/{send['id']}/execute", {"recipient_contacts": [f"renter{i}@example.com" for i in range(1, 41)] + ["optout@example.com", "bounce@example.com"]})
    post("/analytics/snapshots", {"campaign_id": ev["id"], "channel": "email", "sent_count": 4200, "open_count": 1890, "click_count": 512, "conversion_count": 63, "revenue_usd": 28350})
    test = post("/ab-tests", {"campaign_id": ev["id"], "variant_a_draft_id": a["id"], "variant_b_draft_id": b["id"], "metric": "click_rate"})
    post(f"/ab-tests/{test['id']}/record-result", {"variant": "a", "sent_delta": 1200, "converted_delta": 96})
    post(f"/ab-tests/{test['id']}/record-result", {"variant": "b", "sent_delta": 1200, "converted_delta": 151})
    post(f"/ab-tests/{test['id']}/evaluate", {})

    lux = campaign("Weekend luxury rentals", "sms", "Fill weekend luxury inventory at Dallas and Houston", "Executive members")
    sms = draft(lux, "VOYQI: S-Class & Taycan open this weekend at DFW and Galleria. Reserve in the app. Reply STOP to opt out.")
    post("/sends", {"campaign_id": lux["id"], "content_draft_id": sms["id"], "channel": "sms", "scheduled_at": (now + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0).isoformat()})
    post("/analytics/snapshots", {"campaign_id": lux["id"], "channel": "sms", "sent_count": 1180, "open_count": 0, "click_count": 141, "conversion_count": 37, "revenue_usd": 16650})

    win = campaign("Win-back: lapsed subscribers", "push", "Bring back cancelled subscribers with a first-month credit", "Lapsed subscribers", status="paused")
    draft(win, "We kept your favourite class warm. Come back this month and your first week is on us.")
    reel = campaign("Portable Mobility reel", "social", "Awareness for the Texas launch")
    social = draft(reel, "One membership. Every class. Every hub in Texas. #VOYQI #PortableMobility")
    account = post("/social/accounts", {"platform": "instagram", "handle": "@voyqi"})
    post("/social/posts", {"social_account_id": account["id"], "content_draft_id": social["id"], "scheduled_at": (now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0).isoformat()})
    post("/notes", {"author": "marketing.manager@voyqi.example", "body": "Variant B (\"Keep the keys\") is winning on clicks — use it for the Dallas wave.", "campaign_id": ev["id"]})
    for name, email, page in (("Ava Patel", "ava.patel@example.com", "/ev-subscription"), ("Noah Kim", "noah.kim@example.com", "/ev-subscription"), ("Mia Garcia", "mia.garcia@example.com", "/luxury-rentals")):
        api.call("gateway", "POST", f"{M}/public/lead-captures", {"name": name, "email": email, "landing_page": page, "campaign_id": ev["id"], "consent_marketing": True})
    print("  marketing     demo workspace: segments, templates, 4 campaigns, sends, results, A/B test, social post, leads", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", default="voyqi-demo")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--password", default="Voyqi-Demo-2026!", help="password for every seeded login (min 10 chars)")
    parser.add_argument("--email-domain", default="voyqi.example")
    args = parser.parse_args()
    api = Api(args.host, args.tenant)

    print(f"tenant {args.tenant}: role logins (password: {args.password})")
    accounts = seed_users_and_roles(api, args.password, args.email_domain)
    print("sample business data")
    seed_business_data(api, accounts)
    seed_marketing(api)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
