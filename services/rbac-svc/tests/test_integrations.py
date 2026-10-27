"""Integration credentials: encrypted at rest, masked in the API, merged
on update, tested live, and readable decrypted only on /internal/."""
from sqlalchemy import select

from src.core.config import get_settings
from src.models import AuditLogEntry, IntegrationCredential
from src.services import integration_checks
from tests.conftest import TENANT_HEADERS

URL = "/api/v1/rbac/integrations"
INTERNAL = {"X-Internal-Token": "test-internal-token"}
TWILIO = {"account_sid": "AC123", "auth_token": "super-secret-token-9876", "from_number": "+15125550100"}


async def test_catalog_lists_every_integration_unconfigured(client):
    items = {i["provider"]: i for i in (await client.get(URL, headers=TENANT_HEADERS)).json()}
    assert {"anthropic", "smtp", "twilio", "fcm", "meta", "linkedin", "x", "openai", "stripe"} <= set(items)
    assert items["twilio"]["configured"] is False and items["twilio"]["values"]["voice"] == "Polly.Joanna"


async def test_required_fields_are_enforced(client):
    resp = await client.put(f"{URL}/twilio", json={"values": {"account_sid": "AC123"}}, headers=TENANT_HEADERS)
    assert resp.status_code == 422 and "auth_token" in resp.json()["detail"]


async def test_secrets_are_masked_and_encrypted_at_rest(client, db_session):
    saved = (await client.put(f"{URL}/twilio", json={"values": TWILIO}, headers={**TENANT_HEADERS, "X-User-Id": "admin-1"})).json()
    assert saved["values"]["auth_token"] == {"set": True, "preview": "…9876"}
    assert saved["values"]["account_sid"] == "AC123" and saved["updated_by"] == "admin-1"
    listed = (await client.get(URL, headers=TENANT_HEADERS)).text
    assert "super-secret-token" not in listed
    row = (await db_session.execute(select(IntegrationCredential))).scalar_one()
    assert "super-secret-token" not in row.config_encrypted and "AC123" not in row.config_encrypted
    audit = (await db_session.execute(select(AuditLogEntry))).scalars().all()
    assert any(a.action == "integration.updated" for a in audit)
    assert not any("super-secret" in (a.detail or "") for a in audit)


async def test_a_blank_secret_keeps_the_stored_one(client):
    await client.put(f"{URL}/twilio", json={"values": TWILIO}, headers=TENANT_HEADERS)
    await client.put(f"{URL}/twilio", json={"values": {**TWILIO, "auth_token": "", "from_number": "+15125550111"}}, headers=TENANT_HEADERS)
    config = (await client.get("/internal/integrations/twilio", params={"tenant_id": TENANT_HEADERS["X-Tenant-Id"]}, headers=INTERNAL)).json()
    assert config[TENANT_HEADERS["X-Tenant-Id"]]["auth_token"] == "super-secret-token-9876"
    assert config[TENANT_HEADERS["X-Tenant-Id"]]["from_number"] == "+15125550111"


async def test_internal_endpoint_needs_the_token_and_skips_disabled(client):
    await client.put(f"{URL}/twilio", json={"values": TWILIO}, headers=TENANT_HEADERS)
    assert (await client.get("/internal/integrations/twilio")).status_code == 403
    assert (await client.get("/internal/integrations/twilio", headers={"X-Internal-Token": "wrong"})).status_code == 403
    assert TENANT_HEADERS["X-Tenant-Id"] in (await client.get("/internal/integrations/twilio", headers=INTERNAL)).json()
    await client.put(f"{URL}/twilio", json={"values": {}, "enabled": False}, headers=TENANT_HEADERS)
    assert (await client.get("/internal/integrations/twilio", headers=INTERNAL)).json() == {}


async def test_connection_test_records_the_outcome(client, monkeypatch):
    async def fake_twilio(config):
        assert config["auth_token"] == "super-secret-token-9876"
        return False, "HTTP 401: Authenticate"

    monkeypatch.setitem(integration_checks.CHECKS, "twilio", fake_twilio)
    await client.put(f"{URL}/twilio", json={"values": TWILIO}, headers=TENANT_HEADERS)
    tested = (await client.post(f"{URL}/twilio/test", headers=TENANT_HEADERS)).json()
    assert (tested["test_status"], tested["test_detail"]) == ("failed", "HTTP 401: Authenticate")
    assert (await client.post(f"{URL}/smtp/test", headers=TENANT_HEADERS)).status_code == 422  # not configured


async def test_tenants_do_not_see_each_others_credentials(client):
    await client.put(f"{URL}/twilio", json={"values": TWILIO}, headers=TENANT_HEADERS)
    other = {i["provider"]: i for i in (await client.get(URL, headers={"X-Tenant-Id": "other"})).json()}
    assert other["twilio"]["configured"] is False


async def test_removal_and_unknown_providers(client):
    await client.put(f"{URL}/twilio", json={"values": TWILIO}, headers=TENANT_HEADERS)
    assert (await client.delete(f"{URL}/twilio", headers=TENANT_HEADERS)).status_code == 204
    assert (await client.get("/internal/integrations/twilio", headers=INTERNAL)).json() == {}
    assert (await client.put(f"{URL}/myspace", json={"values": {}}, headers=TENANT_HEADERS)).status_code == 404


async def test_without_an_encryption_key_nothing_is_stored(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "integrations_key", "")
    resp = await client.put(f"{URL}/twilio", json={"values": TWILIO}, headers=TENANT_HEADERS)
    assert resp.status_code == 503 and "RBAC_INTEGRATIONS_KEY" in resp.json()["detail"]
