"""Twilio's delivery and call reports: signed, matched to the message,
and readable back through /statuses."""
import base64
import hashlib
import hmac
from urllib.parse import parse_qs, urlencode

from src.core import integrations
from src.services import providers
from tests.conftest import TENANT_HEADERS
from tests.test_providers_live import TWILIO, configured, outbound  # noqa: F401 (fixtures)

TENANT = TENANT_HEADERS["X-Tenant-Id"]
BASE = "https://api.voyqi.test"
TW = {**TWILIO, "status_callback_base_url": BASE}
URL = f"{BASE}/api/v1/notifications/public/twilio/{TENANT}/status"


def _sign(params: dict) -> str:
    payload = URL + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(hmac.new(TW["auth_token"].encode(), payload.encode(), hashlib.sha1).digest()).decode()


async def _post(client, params, signature=None):
    return await client.post(f"/api/v1/notifications/public/twilio/{TENANT}/status", content=urlencode(params),
                             headers={"Content-Type": "application/x-www-form-urlencoded", "X-Twilio-Signature": signature if signature is not None else _sign(params)})


async def test_sends_ask_twilio_to_report_back(client, configured, outbound):  # noqa: F811
    configured["twilio"] = TW
    await providers.send_sms(recipient="+15125550101", body="x", tenant_id=TENANT)
    assert parse_qs(outbound[-1].content.decode())["StatusCallback"] == [URL]
    await providers.send_voice(recipient="+15125550101", body="Hi", tenant_id=TENANT, record=True)
    form = parse_qs(outbound[-1].content.decode())
    assert form["MachineDetection"] == ["Enable"] and form["Record"] == ["true"] and form["RecordingStatusCallback"] == [URL]
    assert "may be recorded" in form["Twiml"][0]
    configured["twilio"] = TWILIO  # no public URL set: no callback requested
    await providers.send_sms(recipient="+15125550101", body="x", tenant_id=TENANT)
    assert "StatusCallback" not in parse_qs(outbound[-1].content.decode())


async def test_signed_call_report_updates_the_notification(client, configured, outbound):  # noqa: F811
    configured["twilio"] = TW
    sent = (await client.post("/api/v1/notifications/send-direct", json={"channel": "voice", "recipient": "+15125550101", "body": "Hello"}, headers=TENANT_HEADERS)).json()
    params = {"CallSid": "SM123", "CallStatus": "completed", "AnsweredBy": "human", "CallDuration": "42"}
    assert (await _post(client, params)).status_code == 204
    [s] = (await client.post("/api/v1/notifications/statuses", json={"ids": [sent["id"]]}, headers=TENANT_HEADERS)).json()
    assert (s["provider_status"], s["answered_by"], s["duration_seconds"]) == ("completed", "human", 42)


async def test_unsigned_or_forged_reports_are_refused(client, configured, outbound):  # noqa: F811
    configured["twilio"] = TW
    params = {"MessageSid": "SM123", "MessageStatus": "delivered"}
    assert (await _post(client, params, signature="forged")).status_code == 403
    assert (await _post(client, {**params, "MessageStatus": "undelivered"}, signature=_sign(params))).status_code == 403
    configured["twilio"] = TWILIO  # no public URL configured: nothing to verify against
    assert (await _post(client, params)).status_code == 403


async def test_undelivered_texts_count_as_failed(client, configured, outbound):  # noqa: F811
    configured["twilio"] = TW
    await client.post("/api/v1/notifications/send-direct", json={"channel": "sms", "recipient": "+15125550101", "body": "x"}, headers=TENANT_HEADERS)
    await _post(client, {"MessageSid": "SM123", "MessageStatus": "undelivered", "ErrorCode": "30003", "ErrorMessage": "Unreachable"})
    summary = (await client.get("/api/v1/notifications/summary?hours=1", headers=TENANT_HEADERS)).json()
    assert summary["channels"]["sms"]["failed"] == 1 and summary["channels"]["sms"]["delivered"] == 0
    assert integrations  # module used by the fixture
