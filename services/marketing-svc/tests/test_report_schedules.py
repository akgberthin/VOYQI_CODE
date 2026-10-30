"""Named reports on a schedule, emailed with a CSV or PDF attached."""
import base64
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.services import report_schedules
from tests.conftest import TENANT_HEADERS
from tests.test_delivery_and_scheduler import notif  # noqa: F401 (fixture)

M = "/api/v1/marketing/reports/schedules"
H = TENANT_HEADERS


async def test_schedules_are_validated(client):
    for bad in ({"name": "", "recipients": ["a@x.com"]}, {"name": "R", "recipients": []}, {"name": "R", "recipients": ["nope"]},
                {"name": "R", "recipients": ["a@x.com"], "frequency": "hourly"}, {"name": "R", "recipients": ["a@x.com"], "channels": ["fax"]},
                {"name": "R", "recipients": ["a@x.com"], "days": 0}, {"name": "R", "recipients": ["a@x.com"], "format": "docx"}):
        assert (await client.post(M, json=bad, headers=H)).status_code == 422, bad


async def test_a_pdf_report_is_emailed_to_each_recipient(client, notif):  # noqa: F811
    s = (await client.post(M, json={"name": "Weekly email performance", "frequency": "weekly", "days": 7, "channels": ["email"],
                                    "recipients": ["CMO@voyqi.com", "growth@voyqi.com"], "format": "pdf"}, headers=H)).json()
    assert s["recipients"] == ["cmo@voyqi.com", "growth@voyqi.com"]
    r = (await client.post(f"{M}/{s['id']}/run", headers=H)).json()
    assert r["sent"] == 2
    call = notif["calls"][-1]
    att = call["options"]["attachments"][0]
    assert call["subject"] == "Weekly email performance — last 7 days" and att["content_type"] == "application/pdf"
    assert att["filename"].startswith("weekly-email-performance-") and base64.b64decode(att["data_base64"]).startswith(b"%PDF")
    listed = (await client.get(M, headers=H)).json()
    assert listed[0]["last_sent_at"] is not None


async def test_csv_format_and_editing_and_deleting(client, notif):  # noqa: F811
    s = (await client.post(M, json={"name": "Daily", "frequency": "daily", "days": 1, "recipients": ["a@x.com"], "format": "csv"}, headers=H)).json()
    await client.post(f"{M}/{s['id']}/run", headers=H)
    att = notif["calls"][-1]["options"]["attachments"][0]
    assert att["content_type"] == "text/csv" and base64.b64decode(att["data_base64"]).startswith(b"section,key,metric,value")
    r = (await client.patch(f"{M}/{s['id']}", json={"active": False, "format": "pdf"}, headers=H)).json()
    assert r["active"] is False and r["format"] == "pdf"
    assert (await client.delete(f"{M}/{s['id']}", headers=H)).status_code == 204
    assert (await client.get(M, headers=H)).json() == []


async def test_only_due_active_schedules_are_sent(client, notif, db_engine):  # noqa: F811
    await client.post(M, json={"name": "W", "frequency": "weekly", "recipients": ["a@x.com"]}, headers=H)
    off = (await client.post(M, json={"name": "Off", "frequency": "daily", "recipients": ["b@x.com"]}, headers=H)).json()
    await client.patch(f"{M}/{off['id']}", json={"active": False}, headers=H)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    assert await report_schedules.send_due(factory, now) == 1
    assert await report_schedules.send_due(factory, now + timedelta(days=3)) == 0
    assert await report_schedules.send_due(factory, now + timedelta(days=8)) == 1


async def test_pdf_download(client):
    r = await client.get("/api/v1/marketing/reports.pdf?days=30", headers=H)
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and r.headers["content-type"] == "application/pdf"
