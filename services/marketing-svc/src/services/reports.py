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
