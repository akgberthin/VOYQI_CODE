#!/usr/bin/env python3
"""Guided demo of the running platform, scene by scene, through the
API gateway exactly as a frontend would call it.

    python3 scripts/seed_demo.py                 # once
    python3 scripts/demo_walkthrough.py          # every scene
    python3 scripts/demo_walkthrough.py --scene 3 --pause

Scenes: 1 roles & access · 2 lead → CRM → AI scoring · 3 credit
origination with the tier-3 human gate · 4 concierge inbox · 5 live
telematics · 6 AI governance · 7 break-glass access & audit.

Standard library only. Without ANTHROPIC_API_KEY the agents run their
deterministic fallback and say so in their reasoning; with the key the
same flows run on Claude.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PASSWORD = "Voyqi-Demo-2026!"
DOMAIN = "voyqi.example"
PAUSE = False


def title(text: str) -> None:
    print(f"\n\033[1;36m{'═' * 70}\n  {text}\n{'═' * 70}\033[0m")


def step(text: str) -> None:
    if PAUSE:
        input("\033[2m  ⏎ next\033[0m")
    print(f"\n\033[1m▶ {text}\033[0m")


def show(label: str, value) -> None:
    print(f"   \033[33m{label}:\033[0m {value}")


def ok(text: str) -> None:
    print(f"   \033[32m✔ {text}\033[0m")


def blocked(text: str) -> None:
    print(f"   \033[31m✖ {text}\033[0m")


class Api:
    def __init__(self, base: str, tenant: str):
        self.base, self.tenant, self.tokens = base.rstrip("/"), tenant, {}
        self.last_lead = None  # (lead id, email) from scene 2, linked into scene 3

    def call(self, method: str, path: str, body=None, as_role: str | None = None):
        headers = {"Content-Type": "application/json", "X-Tenant-Id": self.tenant}
        if as_role:
            headers["Cookie"] = f"access_token={self.token(as_role)}"
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None), resp.headers
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw), exc.headers
            except ValueError:
                return exc.code, raw.decode(errors="replace"), exc.headers

    def token(self, role: str) -> str:
        if role not in self.tokens:
            status, _, headers = self.call("POST", "/api/v1/auth/login", {"email": f"{role}@{DOMAIN}", "password": PASSWORD})
            if status != 200:
                sys.exit(f"login as {role} failed ({status}) — run scripts/seed_demo.py first")
            cookie = next(c for c in headers.get_all("Set-Cookie") if c.startswith("access_token="))
            self.tokens[role] = cookie.split(";", 1)[0].split("=", 1)[1]
        return self.tokens[role]


def wait_for(fetch, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = fetch()
        if found:
            return found
        time.sleep(1)
    return None


def decision_for(api: Api, predicate, timeout: float = 30.0):
    def fetch():
        _, logs, _ = api.call("GET", "/api/v1/ai/decision-log?limit=50", as_role="super.admin")
        return next((d for d in logs or [] if predicate(d)), None)
    return wait_for(fetch, timeout)


def plain(reasoning: str) -> str:
    """Reasoning without the "[deterministic fallback …]" prefix."""
    return reasoning.split("] ", 1)[1] if reasoning.startswith("[") and "] " in reasoning else reasoning


def show_decision(d: dict) -> None:
    show("AI agent", d["agent_key"])
    show("action", d.get("action_taken"))
    show("reasoning", (d.get("reasoning") or "")[:220])


# ── scenes ─────────────────────────────────────────────────────────────

def scene_roles(api: Api) -> None:
    title("1 · Every role logs in and sees only what it should")
    checks = [
        ("fleet.manager", "GET", "/api/v1/fleet/vehicles", "fleet vehicles"),
        ("fleet.manager", "GET", "/api/v1/hrm/employees", "HR employee records"),
        ("hr.manager", "GET", "/api/v1/hrm/employees", "HR employee records"),
        ("hr.manager", "GET", "/api/v1/fleet/vehicles", "fleet vehicles"),
        ("marketing.manager", "GET", "/api/v1/marketing/campaigns", "marketing campaigns"),
        ("dealer.gm", "GET", "/api/v1/dms/rooftops", "dealer rooftops"),
        ("executive", "GET", "/api/v1/rbac/roles", "role administration"),
        ("member", "GET", "/api/v1/subscription/members", "the full member list"),
        ("super.admin", "GET", "/api/v1/rbac/roles", "role administration"),
    ]
    step("Each user signs in; the gateway checks their role's permissions on every call")
    for role, method, path, what in checks:
        status, _, _ = api.call(method, path, as_role=role)
        (ok if status == 200 else blocked)(f"{role:<18} {what:<26} → {'allowed' if status == 200 else f'denied ({status})'}")

    step("A member can see their own profile — never another member's")
    profiles = {}
    for role in ("member", "member.two"):
        _, me, _ = api.call("GET", "/api/v1/auth/me", as_role=role)
        status, profile, _ = api.call("GET", f"/api/v1/subscription/members/by-user-id/{me['user_id']}", as_role=role)
        profiles[role] = profile["id"]
        ok(f"{role:<18} own profile              → allowed ({status})")
    for path, what in ((f"/api/v1/subscription/members/{profiles['member.two']}", "profile"),
                       (f"/api/v1/subscription/members/{profiles['member.two']}/invoices", "invoices")):
        status, _, _ = api.call("GET", path, as_role="member")
        blocked(f"member             member.two's {what:<11} → denied ({status})")

    step("Logging out revokes the token immediately")
    token = api.token("marketing.manager")
    api.call("POST", "/api/v1/auth/logout", as_role="marketing.manager")
    status, _, _ = api.call("GET", "/api/v1/marketing/campaigns", as_role="marketing.manager")
    blocked(f"same token after logout → {status}")
    api.tokens.pop("marketing.manager", None)
    del token


def scene_lead(api: Api) -> None:
    title("2 · A website lead becomes a scored, routed CRM lead — automatically")
    email = f"demo.{uuid.uuid4().hex[:6]}@example.com"
    step("A visitor fills the landing-page form (public, no login)")
    status, capture, _ = api.call("POST", "/api/v1/marketing/public/lead-captures", {
        "name": "Riya Sharma", "email": email, "phone": "+15125550123",
        "landing_page": "/ev-subscription", "consent_marketing": True,
    })
    show("captured", f"{capture['name']} <{email}> (HTTP {status})")

    step("Event on Kafka → Revenue Agent creates the CRM lead, checks duplicates, scores and routes it")
    d = decision_for(api, lambda d: d["trigger_event_type"] == "lead.captured" and (d.get("trigger_context") or {}).get("email") == email)
    if not d:
        return blocked("no decision yet — is ai-orchestration-svc running?")
    show_decision(d)
    lead_id = d["trigger_context"]["lead_id"]
    api.last_lead = (lead_id, email)
    _, lead, _ = api.call("GET", f"/api/v1/crm/leads/{lead_id}", as_role="super.admin")
    show("CRM lead", {k: lead.get(k) for k in ("name", "full_name", "source", "score", "routed_to_team", "status") if k in lead})
    ok("no one typed anything into the CRM")


def scene_origination(api: Api) -> None:
    title("3 · Credit origination — AI assists, a named human decides")
    if api.last_lead is None:
        scene_lead(api)
    lead_id, email = api.last_lead
    step("The same customer from scene 2 applies to subscribe ($18,000 / 12 months)")
    status, case, _ = api.call("POST", "/api/v1/org/cases", {
        "origination_type": "subscription_enrollment", "full_name": "Riya Sharma", "amount_usd": 18000,
        "email": email, "phone": "+15125550123", "external_ref_id": lead_id,
    }, as_role="super.admin")
    if status != 201:
        return blocked(f"case not created: {status} {case}")
    show("case", f"{case['full_name']} · ${case['amount_usd']:,.0f} · {case['status']}")

    step("AI pre-qualifies it")
    case = wait_for(lambda: (lambda c: c if c.get("prequal_verdict") else None)(api.call("GET", f"/api/v1/org/cases/{case['id']}", as_role="super.admin")[1])) or case
    show("verdict", case.get("prequal_verdict"))
    show("reasoning", (case.get("prequal_reasoning") or "")[:200])

    step("Underwriting requested → AI risk-scores it")
    _, decision, _ = api.call("POST", f"/api/v1/org/cases/{case['id']}/decisions", {}, as_role="super.admin")
    decision = wait_for(lambda: (lambda x: x if x.get("risk_score") is not None else None)(api.call("GET", f"/api/v1/org/decisions/{decision['id']}", as_role="super.admin")[1])) or decision
    show("risk score", decision.get("risk_score"))
    show("AI recommends", decision.get("recommended_outcome"))

    step("Can the decision be finalized without a named accountable person? (tier 3 — fair lending)")
    status, body, _ = api.call("POST", f"/api/v1/org/decisions/{decision['id']}/finalize", {"approve": True, "decided_by": "underwriter"}, as_role="super.admin")
    blocked(f"refused ({status}): {body.get('detail') if isinstance(body, dict) else body}")

    step("The credit manager finalizes it under their name")
    status, final, _ = api.call("POST", f"/api/v1/org/decisions/{decision['id']}/finalize", {
        "approve": True, "accountable_role": "credit_manager", "decided_by": "credit.manager", "reasoning": "verified income and DTI",
    }, as_role="super.admin")
    ok(f"{final['status']} by {final['decided_by']} (accountable: {final['accountable_role']})")


def scene_concierge(api: Api) -> None:
    title("4 · Concierge inbox — AI drafts replies, escalates what needs a human")
    _, convo, _ = api.call("POST", "/api/v1/crm/conversations", {"channel": "email", "subject": "Billing problem"}, as_role="super.admin")
    for text in ("Hi, can I switch my subscription from the Model 3 to a Model Y next month?",
                 "This is the third time I've been double charged. I want a refund now or I'm calling my lawyer."):
        step(f"Customer writes: “{text}”")
        _, msg, _ = api.call("POST", f"/api/v1/crm/conversations/{convo['id']}/messages", {"body": text, "sender": "customer"}, as_role="super.admin")
        d = decision_for(api, lambda d: d["agent_key"] == "concierge_agent" and (d.get("trigger_context") or {}).get("message_id") == msg["id"])
        if d:
            show_decision(d)
    _, drafts, _ = api.call("GET", "/api/v1/crm/draft-replies", as_role="super.admin")
    mine = [x for x in drafts or [] if x["conversation_id"] == convo["id"]]
    for draft in mine:
        show("draft waiting for agent approval", draft["draft_body"][:160])
    _, convo, _ = api.call("GET", f"/api/v1/crm/conversations/{convo['id']}", as_role="super.admin")
    show("conversation", f"status={convo['status']} priority={convo['priority']} team={convo.get('routed_to_team')}")


def scene_telematics(api: Api) -> None:
    title("5 · Live telematics — vehicles report over MQTT")
    step("12 simulated vehicles stream GPS, battery and diagnostics for ~30 seconds")
    script = Path(__file__).with_name("telematics_simulator.py")
    subprocess.run([sys.executable, str(script), "--tenant", api.tenant, "--vehicles", "12", "--ticks", "15", "--interval", "2",
                    "--wander-rate", "0.08", "--tamper-rate", "0.01", "--transport", "mqtt", "--mqtt-host", "localhost"], check=False)
    step("What the AI did with it")
    time.sleep(3)
    _, logs, _ = api.call("GET", "/api/v1/ai/decision-log?limit=50", as_role="super.admin")
    for d in [x for x in logs or [] if x["trigger_event_type"] in ("flt.telemetry_anomaly_requested", "vehicle.tamper_detected")][:4]:
        show_decision(d)
        print()


def scene_governance(api: Api) -> None:
    title("6 · AI governance — every agent, every decision, under control")
    step("The agent roster and how autonomous each action is")
    _, roster, _ = api.call("GET", "/api/v1/ai/roster", as_role="super.admin")
    agents = roster.get("agents", roster) if isinstance(roster, dict) else roster
    show("agents", len(agents))
    for a in agents[:14]:
        print(f"     · {a.get('name') or a.get('key')}")
    _, policy, _ = api.call("GET", "/api/v1/ai/tier-policy", as_role="super.admin")
    tiers = [a["tier"] for a in policy["actions"]]
    show("actions", f"{len(tiers)} total — tier 1 (autonomous) {tiers.count(1)}, tier 2 (approval) {tiers.count(2)}, tier 3 (named human) {tiers.count(3)}")

    step("Try to make a credit decision autonomous")
    status, body, _ = api.call("PUT", "/api/v1/ai/tier-policy/sales-originations-intelligence/credit.decision", {"tier": 1, "reason": "speed"}, as_role="super.admin")
    blocked(f"refused ({status}): {body['detail']}")
    step("Try to remove an approval gate without a track record")
    status, body, _ = api.call("PUT", "/api/v1/ai/tier-policy/sales-originations-intelligence/deal.structure_recommend", {"tier": 1, "reason": "speed"}, as_role="super.admin")
    blocked(f"refused ({status}): {body['detail']}")

    step("Recommendations waiting for a human")
    _, recs, _ = api.call("GET", "/api/v1/ai/recommendations?status=pending", as_role="super.admin")
    show("pending", len(recs or []))
    for r in (recs or [])[:5]:
        print(f"     · [{r['agent_key']}] {r['action_type']} — {plain(r['reasoning'])[:90]}")

    step("Audit trail — the latest AI decisions")
    _, logs, _ = api.call("GET", "/api/v1/ai/decision-log?limit=6", as_role="super.admin")
    for d in logs or []:
        print(f"     · {d['created_at'][:19]}  {d['agent_key']:<28} {d['trigger_event_type']}")


def scene_break_glass(api: Api) -> None:
    title("7 · Emergency (break-glass) access — granted, reviewed, closed, audited")
    step("An admin activates emergency access to a user's account")
    status, session, _ = api.call("POST", "/api/v1/identity/break-glass", {
        "user_id": "member@voyqi.example", "activated_by": "super.admin@voyqi.example",
        "reason": "member locked out during a live rental", "duration_minutes": 30,
    }, as_role="super.admin")
    show("session", f"{session['status']} · off-hours={session['off_hours']} · expires {session['expires_at'][:19]}")

    step("Compliance Agent flags it for review")
    rec = wait_for(lambda: next((r for r in api.call("GET", "/api/v1/ai/recommendations?status=pending", as_role="super.admin")[1] or []
                                 if json.dumps(r["proposed_action"]).find(session["id"]) >= 0), None))
    if not rec:
        return blocked("no review card yet")
    show("review card", f"{rec['action_type']} — risk {rec['risk_score']}")
    show("reasoning", rec["reasoning"][:200])

    step("The compliance officer approves the review action → the session is ended")
    status, decided, _ = api.call("POST", f"/api/v1/ai/recommendations/{rec['id']}/approve", {"reason_code": "reviewed", "accountable_role": "compliance_officer"}, as_role="super.admin")
    show("recommendation", decided.get("status") if isinstance(decided, dict) else decided)
    _, sessions, _ = api.call("GET", "/api/v1/identity/break-glass", as_role="super.admin")
    final = next(s for s in sessions if s["id"] == session["id"])
    ok(f"session {final['status']} (ended by {final.get('ended_by')})")


SCENES = [scene_roles, scene_lead, scene_origination, scene_concierge, scene_telematics, scene_governance, scene_break_glass]


def main() -> int:
    global PAUSE
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway", default="http://localhost:8000")
    parser.add_argument("--tenant", default="voyqi-demo")
    parser.add_argument("--scene", type=int, action="append", help="scene number(s) to run; default all")
    parser.add_argument("--pause", action="store_true", help="wait for Enter before each step")
    args = parser.parse_args()
    PAUSE = args.pause
    sys.stdout.reconfigure(line_buffering=True)  # keep our lines in order with the simulator's
    api = Api(args.gateway, args.tenant)
    for n in args.scene or range(1, len(SCENES) + 1):
        SCENES[n - 1](api)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
