"""Providers with a tenant's integrations configured: the requests that
actually leave for Twilio and FCM, and console mode without them."""
import json
from urllib.parse import parse_qs

import httpx
import pytest

from src.core import integrations
from src.services import providers
from tests.conftest import TENANT_HEADERS

TENANT = TENANT_HEADERS["X-Tenant-Id"]
TWILIO = {"account_sid": "AC1", "auth_token": "tok", "from_number": "+15125550100", "voice": "Polly.Joanna"}


@pytest.fixture
def outbound(monkeypatch):
    """Captures every outbound HTTP request and answers like the provider."""
    sent = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request):
        sent.append(request)
        if "oauth2.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"access_token": "ya29.test"})
        if "fcm.googleapis.com" in str(request.url):
            return httpx.Response(200, json={"name": "projects/p/messages/1"})
        return httpx.Response(201, json={"sid": "SM123"})

    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    return sent


@pytest.fixture
def configured(monkeypatch):
    configs = {}

    async def fake_get(tenant_id, provider):
        return configs.get(provider) if tenant_id == TENANT else None

    monkeypatch.setattr(integrations, "get", fake_get)
    return configs


async def test_without_credentials_messages_are_only_logged(configured, outbound):
    ok, mode, _id, _err = await providers.send_sms(recipient="+15125550101", body="hi", tenant_id=TENANT)
    assert (ok, mode) == (True, "console") and outbound == []


async def test_sms_goes_to_twilio_with_the_tenants_account(configured, outbound):
    configured["twilio"] = TWILIO
    ok, mode, sid, _err = await providers.send_sms(recipient="+15125550101", body="Your car is ready", tenant_id=TENANT)
    assert (ok, mode, sid) == (True, "real_provider", "SM123")
    [req] = outbound
    assert str(req.url) == "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json"
    form = parse_qs(req.content.decode())
    assert form == {"To": ["+15125550101"], "From": ["+15125550100"], "Body": ["Your car is ready"]}


async def test_voice_calls_say_they_are_automated_and_escape_the_script(configured, outbound):
    configured["twilio"] = TWILIO
    await providers.send_voice(recipient="+15125550101", body="Offers <today> & tomorrow", tenant_id=TENANT)
    twiml = parse_qs(outbound[0].content.decode())["Twiml"][0]
    assert str(outbound[0].url).endswith("/Calls.json")
    assert twiml.index("automated call") < twiml.index("Offers")
    assert "&lt;today&gt; &amp; tomorrow" in twiml


async def test_twilio_errors_are_reported_not_hidden(configured, monkeypatch):
    configured["twilio"] = TWILIO
    real_client = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"message": "The 'To' number is not a valid phone number."})), **kw))
    ok, _mode, _sid, err = await providers.send_sms(recipient="123", body="x", tenant_id=TENANT)
    assert ok is False and "not a valid phone number" in err


async def test_push_uses_a_service_account_token_for_fcm_v1(configured, outbound):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    configured["fcm"] = {"service_account_json": json.dumps({"client_email": "svc@p.iam.gserviceaccount.com", "private_key": pem, "project_id": "voyqi-app"})}
    ok, mode, name, _err = await providers.send_push(recipient="device-token", body="Car staged", subject="Ready", tenant_id=TENANT)
    assert (ok, mode, name) == (True, "real_provider", "projects/p/messages/1")
    token_req, send_req = outbound
    assert "jwt-bearer" in token_req.content.decode()
    assert str(send_req.url) == "https://fcm.googleapis.com/v1/projects/voyqi-app/messages:send"
    assert send_req.headers["Authorization"] == "Bearer ya29.test"
    assert json.loads(send_req.content)["message"] == {"token": "device-token", "notification": {"title": "Ready", "body": "Car staged"}}


async def test_send_direct_api_delivers_content_on_each_channel(client, configured, outbound):
    configured["twilio"] = TWILIO
    resp = await client.post("/api/v1/notifications/send-direct", json={"channel": "sms", "recipient": "+15125550101", "body": "Spring offer", "source": "marketing:send-1"}, headers=TENANT_HEADERS)
    assert resp.status_code == 201 and resp.json()["status"] == "sent" and resp.json()["provider_message_id"] == "SM123"
    voice = await client.post("/api/v1/notifications/send-direct", json={"channel": "voice", "recipient": "+15125550101", "body": "Hello"}, headers=TENANT_HEADERS)
    assert voice.json()["status"] == "sent"
    bad = await client.post("/api/v1/notifications/send-direct", json={"channel": "fax", "recipient": "x", "body": "y"}, headers=TENANT_HEADERS)
    assert bad.status_code == 400


async def test_delivery_summary_separates_real_console_and_failed(client, configured, outbound):
    await client.post("/api/v1/notifications/send-direct", json={"channel": "sms", "recipient": "+1", "body": "x"}, headers=TENANT_HEADERS)  # console
    configured["twilio"] = TWILIO
    await client.post("/api/v1/notifications/send-direct", json={"channel": "sms", "recipient": "+2", "body": "x"}, headers=TENANT_HEADERS)  # real
    summary = (await client.get("/api/v1/notifications/summary?hours=1", headers=TENANT_HEADERS)).json()
    assert summary["channels"]["sms"] == {"delivered": 1, "console": 1, "failed": 0, "opted_out": 0}
    listed = (await client.get("/api/v1/notifications/notifications", headers=TENANT_HEADERS)).json()
    assert {"error_message", "delivery_mode"} <= set(listed[0])


async def test_email_options_set_sender_name_reply_to_and_preview(configured, monkeypatch):
    configured["smtp"] = {"host": "smtp.example", "port": 587, "from_address": "news@voyqi.com", "use_tls": "false"}
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def sendmail(self, frm, to, raw): sent.update(frm=frm, to=to, raw=raw)

    monkeypatch.setattr(providers.smtplib, "SMTP", FakeSMTP)
    ok, mode, _id, _err = await providers.send_email(recipient="ava@example.com", subject="Your plan", body="<p>Hello</p>", tenant_id=TENANT,
                                                     from_name="VOYQI Concierge\r\nBcc: x@evil", reply_to="care@voyqi.com", preview_text="Ready at Brickell")
    assert (ok, mode) == (True, "real_provider")
    # The typed name can't start a header of its own; it stays quoted inside From.
    assert '\nFrom: "VOYQI Concierge Bcc: x@evil" <news@voyqi.com>' in sent["raw"] and "\nBcc:" not in sent["raw"]
    assert "Reply-To: care@voyqi.com" in sent["raw"]
    assert sent["raw"].index("Ready at Brickell") < sent["raw"].index("Hello")


def test_preview_text_only_goes_into_html_mail():
    assert providers.with_preheader("Plain body", "Preview") == "Plain body"
    assert providers.with_preheader("<p>x</p>", "<b>Preview</b>").startswith('<div style="display:none')


async def test_push_carries_the_deep_link_and_voice_the_chosen_voice(client, configured, outbound):
    configured["twilio"] = TWILIO
    r = await client.post("/api/v1/notifications/send-direct", json={"channel": "voice", "recipient": "+15125550101", "body": "Hola",
                                                                       "options": {"voice": "Polly.Lupe", "language": "es-US"}}, headers=TENANT_HEADERS)
    assert r.json()["status"] == "sent"
    twiml = parse_qs(outbound[-1].content.decode())["Twiml"][0]
    assert 'voice="Polly.Lupe" language="es-US"' in twiml
    ok, _mode, _id, _err = await providers.send_push(recipient="tok", body="x", subject="t", tenant_id="other-tenant", data={"deep_link": "voyqi://unlock"})
    assert ok


async def test_sms_sender_id_and_mms_image(configured, outbound):
    configured["twilio"] = TWILIO
    await providers.send_sms(recipient="+15125550101", body="Your car", tenant_id=TENANT, sender_id="VOYQI", media_url="https://cdn.voyqi.com/car.jpg")
    form = parse_qs(outbound[-1].content.decode())
    assert form["From"] == ["VOYQI"] and form["MediaUrl"] == ["https://cdn.voyqi.com/car.jpg"]
    await providers.send_sms(recipient="+15125550101", body="x", tenant_id=TENANT, media_url="http://insecure.example/a.jpg")
    assert "MediaUrl" not in parse_qs(outbound[-1].content.decode())


async def test_email_attachments_and_their_limits(configured, monkeypatch, client):
    import base64

    configured["smtp"] = {"host": "smtp.example", "port": 587, "from_address": "news@voyqi.com", "use_tls": "false"}
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def sendmail(self, frm, to, raw): sent["raw"] = raw

    monkeypatch.setattr(providers.smtplib, "SMTP", FakeSMTP)
    pdf = base64.b64encode(b"%PDF-1.4 report").decode()
    ok, _mode, _id, _err = await providers.send_email(recipient="cmo@x.com", subject="Report", body="Attached.", tenant_id=TENANT,
                                                      attachments=[{"filename": "../report.pdf", "content_type": "application/pdf", "data_base64": pdf}])
    assert ok and "multipart/mixed" in sent["raw"] and 'filename="report.pdf"' in sent["raw"]
    assert "application/pdf" in sent["raw"]
    r = await client.post("/api/v1/notifications/send-direct", json={"channel": "email", "recipient": "cmo@x.com", "body": "x",
                                                                       "options": {"attachments": [{"filename": "a.pdf", "data_base64": "not base64!!"}]}}, headers=TENANT_HEADERS)
    assert r.status_code == 400 and "base64" in r.json()["detail"]


async def test_delivery_log_and_kpis_by_sender(client, configured, outbound):
    configured["twilio"] = TWILIO
    send = lambda source, to="+2": client.post("/api/v1/notifications/send-direct", json={"channel": "sms", "recipient": to, "body": "hello there", "source": source}, headers=TENANT_HEADERS)  # noqa: E731
    await send("journey:abc")
    await send("marketing:1234")
    await send("marketing:1234:retry")
    await send("super_admin_test")
    await client.post("/api/v1/notifications/send-direct", json={"channel": "email", "recipient": "a@b.c", "subject": "Hi", "body": "x", "source": "seo_outreach"}, headers=TENANT_HEADERS)  # console

    summary = (await client.get("/api/v1/notifications/summary?hours=1", headers=TENANT_HEADERS)).json()
    t = summary["totals"]
    assert t["total"] == 5 and t["delivered"] == 4 and t["console"] == 1 and t["automated"] == 3
    assert summary["delivery_rate"] == 1.0
    senders = {x["sender"]: x for x in summary["by_sender"]}
    assert senders["automation"]["count"] == 1 and senders["automation"]["automated"] and not senders["campaign"]["automated"]

    log = (await client.get("/api/v1/notifications/notifications", params={"sender": "retry"}, headers=TENANT_HEADERS)).json()
    assert len(log) == 1 and log[0]["sender_label"] == "Automatic retry" and log[0]["automated"] is True and log[0]["preview"] == "hello there"
    emails = (await client.get("/api/v1/notifications/notifications", params={"channel": "email"}, headers=TENANT_HEADERS)).json()
    assert len(emails) == 1 and emails[0]["subject"] == "Hi" and emails[0]["sender"] == "seo_agent"
    assert len((await client.get("/api/v1/notifications/notifications", params={"limit": 2}, headers=TENANT_HEADERS)).json()) == 2


async def test_failed_filter_includes_provider_reported_failures(client, db_session):
    from src.models import Notification
    db_session.add_all([
        Notification(tenant_id=TENANT, channel="sms", template_key="x", recipient="+1", body="a", status="sent", delivery_mode="live", provider_status="undelivered"),
        Notification(tenant_id=TENANT, channel="sms", template_key="x", recipient="+2", body="b", status="failed"),
        Notification(tenant_id=TENANT, channel="sms", template_key="x", recipient="+3", body="c", status="sent", delivery_mode="live"),
    ])
    await db_session.commit()
    failed = (await client.get("/api/v1/notifications/notifications", params={"status": "failed"}, headers=TENANT_HEADERS)).json()
    assert sorted(f["recipient"] for f in failed) == ["+1", "+2"]
    summary = (await client.get("/api/v1/notifications/summary?hours=1", headers=TENANT_HEADERS)).json()
    assert summary["totals"]["failed"] == 2 and summary["delivery_rate"] == round(1 / 3, 4)
