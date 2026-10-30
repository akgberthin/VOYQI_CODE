from tests.conftest import TENANT_HEADERS


async def test_audit_log_records_actions_from_multiple_engines(client):
    await client.post("/api/v1/rbac/tenants", json={"tenant_key": "voyqi-holdings", "name": "VOYQI Holdings", "subsidiary": "holdco"})
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "hrm"}, headers=TENANT_HEADERS)

    log = (await client.get("/api/v1/rbac/audit-log", headers=TENANT_HEADERS)).json()
    actions = {e["action"] for e in log}
    assert "module_grant.enabled" in actions


async def test_audit_log_can_filter_by_actor_type(client):
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "parts"}, headers=TENANT_HEADERS)
    human_only = (await client.get("/api/v1/rbac/audit-log", params={"actor_type": "human"}, headers=TENANT_HEADERS)).json()
    ai_only = (await client.get("/api/v1/rbac/audit-log", params={"actor_type": "ai"}, headers=TENANT_HEADERS)).json()
    assert len(human_only) >= 1
    assert all(e["actor_type"] == "human" for e in human_only)
    assert all(e["actor_type"] == "ai" for e in ai_only)


async def test_audit_log_ordered_most_recent_first(client):
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "recon"}, headers=TENANT_HEADERS)
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "loaners"}, headers=TENANT_HEADERS)
    log = (await client.get("/api/v1/rbac/audit-log", headers=TENANT_HEADERS)).json()
    assert log[0]["resource"] == "loaners"


async def test_audit_log_entries_say_when_they_happened(client):
    await client.post("/api/v1/rbac/module-grants", json={"module_key": "fleet"}, headers=TENANT_HEADERS)
    entry = (await client.get("/api/v1/rbac/audit-log", headers=TENANT_HEADERS)).json()[0]
    assert entry["occurred_at"].startswith("20")
