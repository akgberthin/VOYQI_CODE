"""Marketing report over a period: sends and their outcomes by channel,
social posts by platform, journeys, leads and the top campaigns — with a
daily series of real deliveries."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import AnalyticsSnapshot, Campaign, JourneyEnrollment, LeadCapture, ScheduledSend, SendStatus, SocialAccount, SocialPost


def _v(x):
    return x.value if hasattr(x, "value") else str(x)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


async def build(db: AsyncSession, tenant_id: str, days: int = 30) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    sends = [s for s in (await db.execute(select(ScheduledSend).where(ScheduledSend.tenant_id == tenant_id))).scalars() if s.sent_at and _aware(s.sent_at) >= since]
    channels: dict[str, dict] = defaultdict(lambda: {"sends": 0, "targeted": 0, "delivered": 0, "console": 0, "failed": 0, "suppressed": 0})
    daily: dict[str, int] = defaultdict(int)
    for s in sends:
        c = channels[_v(s.channel)]
        delivered = max(s.recipients_targeted - s.recipients_suppressed - s.recipients_failed - s.recipients_console, 0) if s.status == SendStatus.SENT else 0
        c["sends"] += 1
        c["targeted"] += s.recipients_targeted
        c["delivered"] += delivered
        c["console"] += s.recipients_console
        c["failed"] += s.recipients_failed
        c["suppressed"] += s.recipients_suppressed
        daily[_aware(s.sent_at).date().isoformat()] += delivered

    accounts = {a.id: _v(a.platform) for a in (await db.execute(select(SocialAccount).where(SocialAccount.tenant_id == tenant_id))).scalars()}
    social: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for p in (await db.execute(select(SocialPost).where(SocialPost.tenant_id == tenant_id))).scalars():
        if _aware(p.scheduled_at) >= since:
            key = _v(p.status) if _v(p.status) != "published" else ("published" if p.delivery_mode != "console" else "console")
            social[accounts.get(p.social_account_id, "unknown")][key] += 1

    enrol = [e for e in (await db.execute(select(JourneyEnrollment).where(JourneyEnrollment.tenant_id == tenant_id))).scalars() if _aware(e.enrolled_at) >= since]
    journey_messages = sum(1 for e in enrol for h in e.history if str(h.get("outcome", "")).startswith("sent"))

    names = {c.id: (c.name, _v(c.channel)) for c in (await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_id))).scalars()}
    reach: dict = defaultdict(lambda: {"delivered": 0, "conversions": 0, "revenue_usd": 0.0})
    for snap in (await db.execute(select(AnalyticsSnapshot).where(AnalyticsSnapshot.tenant_id == tenant_id))).scalars():
        if _aware(snap.snapshot_at) >= since:
            r = reach[snap.campaign_id]
            r["delivered"] += snap.sent_count
            r["conversions"] += snap.conversion_count
            r["revenue_usd"] += snap.revenue_usd
    top = sorted(({"campaign_id": str(cid), "name": names.get(cid, ("?", ""))[0], "channel": names.get(cid, ("", "?"))[1], **v} for cid, v in reach.items()),
                 key=lambda x: (x["revenue_usd"], x["delivered"]), reverse=True)[:10]

    leads = [lc for lc in (await db.execute(select(LeadCapture).where(LeadCapture.tenant_id == tenant_id))).scalars() if lc.created_at and _aware(lc.created_at) >= since]
    return {
        "days": days, "since": since.isoformat(),
        "channels": {k: dict(v) for k, v in sorted(channels.items())},
        "social": {k: dict(v) for k, v in sorted(social.items())},
        "journeys": {"enrolled": len(enrol), "completed": sum(1 for e in enrol if e.status == "completed"), "active": sum(1 for e in enrol if e.status == "active"), "messages_sent": journey_messages},
        "leads": len(leads),
        "top_campaigns": top,
        "daily_delivered": [{"date": d, "delivered": n} for d, n in sorted(daily.items())],
    }


def to_csv(report: dict) -> str:
    rows = ["section,key,metric,value"]
    for ch, m in report["channels"].items():
        rows += [f"channel,{ch},{k},{v}" for k, v in m.items()]
    for platform, m in report["social"].items():
        rows += [f"social,{platform},{k},{v}" for k, v in m.items()]
    rows += [f"journeys,all,{k},{v}" for k, v in report["journeys"].items()]
    rows.append(f"leads,all,captured,{report['leads']}")
    for c in report["top_campaigns"]:
        name = c["name"].replace('"', "'")
        rows += [f'campaign,"{name}",{k},{c[k]}' for k in ("delivered", "conversions", "revenue_usd")]
    rows += [f"daily,{d['date']},delivered,{d['delivered']}" for d in report["daily_delivered"]]
    return "\n".join(rows) + "\n"


PERIOD_DAYS = {"weekly": 7, "monthly": 30}


def as_text(r: dict) -> str:
    """The report as an email: deliveries, social, journeys, leads, top campaigns."""
    delivered = sum(c["delivered"] for c in r["channels"].values())
    lines = [f"VOYQI marketing report — last {r['days']} days", "",
             f"Delivered: {delivered:,} (real deliveries only)"]
    for ch, c in r["channels"].items():
        lines.append(f"  {ch}: {c['delivered']:,} delivered, {c['console']:,} console only, {c['failed']:,} failed, {c['suppressed']:,} suppressed")
    if r["social"]:
        lines.append("Social: " + "; ".join(f"{p} {v.get('published', 0)} published" for p, v in r["social"].items()))
    lines += [f"Automation: {r['journeys']['messages_sent']:,} messages, {r['journeys']['completed']:,} journeys completed",
              f"Leads captured: {r['leads']:,}", "", "Top campaigns:"]
    lines += [f"  {c['name']} ({c['channel']}): {c['delivered']:,} delivered, {c['conversions']} conversions, ${c['revenue_usd']:,.0f}"
              for c in r["top_campaigns"][:5]] or ["  none with results in this period"]
    return "\n".join(lines)


async def send(db: AsyncSession, tenant_id: str, days: int, recipients: list[str]) -> dict:
    """Emails the report to each recipient through notification-svc."""
    from src.services import delivery

    body = as_text(await build(db, tenant_id, days))
    results = [await delivery.deliver(tenant_id, "email", to, body, f"VOYQI marketing report — last {days} days", "marketing:report")
               for to in recipients]
    return {"sent": sum(1 for x in results if x.get("status") == "sent"), "failed": [x.get("error_message") for x in results if x.get("status") == "failed"],
            "console": sum(1 for x in results if x.get("status") == "sent" and x.get("delivery_mode") == "console")}


async def send_due(session_factory) -> int:
    """Weekly/monthly reports whose time has come."""
    from src.models import MarketingSettings

    now, sent = datetime.now(timezone.utc), 0
    async with session_factory() as db:
        due = [(s.tenant_id, s.report_frequency, list(s.report_recipients or []))
               for s in (await db.execute(select(MarketingSettings).where(MarketingSettings.report_frequency != "off"))).scalars()
               if s.report_recipients and (s.report_last_sent_at is None or now - _aware(s.report_last_sent_at) >= timedelta(days=PERIOD_DAYS.get(s.report_frequency, 7)))]
    for tenant_id, freq, recipients in due:
        async with session_factory() as db:
            row = (await db.execute(select(MarketingSettings).where(MarketingSettings.tenant_id == tenant_id))).scalar_one()
            row.report_last_sent_at = now  # first, so a slow send isn't repeated by the next pass
            await db.commit()
            await send(db, tenant_id, PERIOD_DAYS.get(freq, 7), recipients)
            sent += 1
    return sent
