"""Named reports emailed on a schedule — each with its own period, the
channels it covers, its recipients and its format (CSV or PDF attached,
always with the summary in the email body)."""
import base64
import io
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import ReportSchedule
from src.services import reports

FREQUENCIES = {"daily": 1, "weekly": 7, "monthly": 30}
FORMATS = ("csv", "pdf")
CHANNELS = ("email", "sms", "push", "voice", "social")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ScheduleError(ValueError):
    pass


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def clean(values: dict, current: ReportSchedule | None = None) -> dict:
    out: dict = {}
    if "name" in values:
        name = str(values["name"] or "").strip()
        if not name:
            raise ScheduleError("name is required")
        out["name"] = name[:120]
    if "frequency" in values:
        if values["frequency"] not in FREQUENCIES:
            raise ScheduleError(f"frequency must be one of {', '.join(FREQUENCIES)}")
        out["frequency"] = values["frequency"]
    if "days" in values:
        days = values["days"]
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365:
            raise ScheduleError("days must be between 1 and 365")
        out["days"] = days
    if "channels" in values:
        chans = list(dict.fromkeys(values["channels"] or []))
        if any(c not in CHANNELS for c in chans):
            raise ScheduleError(f"channels must be among {', '.join(CHANNELS)} (none = all)")
        out["channels"] = chans
    if "recipients" in values:
        emails = []
        for raw in values["recipients"] or []:
            e = str(raw).strip().lower()
            if not EMAIL_RE.match(e):
                raise ScheduleError(f"{e!r} is not an email address")
            if e not in emails:
                emails.append(e)
        if not emails:
            raise ScheduleError("add at least one recipient")
        out["recipients"] = emails
    if "format" in values:
        if values["format"] not in FORMATS:
            raise ScheduleError("format must be csv or pdf")
        out["format"] = values["format"]
    if "active" in values:
        out["active"] = bool(values["active"])
    if current is None:
        for need in ("name", "recipients"):
            if need not in out:
                raise ScheduleError(f"{need} is required")
    return out


def filtered(report: dict, channels: list[str]) -> dict:
    """The report limited to these channels (none = all)."""
    if not channels:
        return report
    return {**report, "channels": {k: v for k, v in report["channels"].items() if k in channels},
            "social": report["social"] if "social" in channels else {},
            "top_campaigns": [c for c in report["top_campaigns"] if c["channel"] in channels]}


def to_pdf(title: str, report: dict) -> bytes:
    """A one-to-two page PDF: headline numbers, channels, social, top campaigns."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=title, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    gold, grid = colors.HexColor("#b08a2e"), colors.HexColor("#d9d9d9")

    def table(rows, widths=None):
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3ecda")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#3d2f0b")),
                               ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9), ("GRID", (0, 0), (-1, -1), 0.4, grid),
                               ("ALIGN", (1, 1), (-1, -1), "RIGHT"), ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5)]))
        return t

    delivered = sum(c["delivered"] for c in report["channels"].values())
    story = [Paragraph(f'<font color="{gold.hexval()}">VOYQI</font> · {title}', styles["Title"]),
             Paragraph(f"Last {report['days']} days · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", styles["Normal"]), Spacer(1, 14),
             table([["Delivered", "Leads", "Journey messages", "Journeys completed"],
                    [f"{delivered:,}", f"{report['leads']:,}", f"{report['journeys']['messages_sent']:,}", f"{report['journeys']['completed']:,}"]]), Spacer(1, 16),
             Paragraph("Channels", styles["Heading2"])]
    rows = [["Channel", "Sends", "Targeted", "Delivered", "Console only", "Failed", "Suppressed"]]
    rows += [[ch, c["sends"], c["targeted"], c["delivered"], c["console"], c["failed"], c["suppressed"]] for ch, c in report["channels"].items()]
    story.append(table(rows) if len(rows) > 1 else Paragraph("No sends in this period.", styles["Normal"]))
    if report["social"]:
        story += [Spacer(1, 12), Paragraph("Social", styles["Heading2"]),
                  table([["Platform", "Published", "Console only", "Scheduled", "Failed"]] +
                        [[p, v.get("published", 0), v.get("console", 0), v.get("scheduled", 0), v.get("failed", 0)] for p, v in report["social"].items()])]
    story += [Spacer(1, 12), Paragraph("Top campaigns", styles["Heading2"])]
    top = [["Campaign", "Channel", "Delivered", "Conversions", "Revenue"]] + [[c["name"][:48], c["channel"], f"{c['delivered']:,}", c["conversions"], f"${c['revenue_usd']:,.0f}"] for c in report["top_campaigns"]]
    story.append(table(top, [200, 60, 70, 70, 80]) if len(top) > 1 else Paragraph("No campaign results recorded in this period.", styles["Normal"]))
    story += [Spacer(1, 14), Paragraph("Delivered counts are real deliveries only; messages logged without a provider are listed separately as console only.", styles["Italic"])]
    doc.build(story)
    return buf.getvalue()


async def render(db: AsyncSession, tenant_id: str, s: ReportSchedule) -> tuple[str, str, dict]:
    """(subject, body, attachment) for a schedule, built from live data."""
    data = filtered(await reports.build(db, tenant_id, s.days), s.channels or [])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", s.name.lower()).strip("-") or "report"
    if s.format == "pdf":
        attachment = {"filename": f"{slug}-{stamp}.pdf", "content_type": "application/pdf", "data_base64": base64.b64encode(to_pdf(s.name, data)).decode()}
    else:
        attachment = {"filename": f"{slug}-{stamp}.csv", "content_type": "text/csv", "data_base64": base64.b64encode(reports.to_csv(data).encode()).decode()}
    return f"{s.name} — last {s.days} days", reports.as_text(data), attachment


async def send(db: AsyncSession, s: ReportSchedule) -> dict:
    from src.services import delivery

    subject, body, attachment = await render(db, s.tenant_id, s)
    results = [await delivery.deliver(s.tenant_id, "email", to, body, subject, f"marketing:report:{s.id}", {"attachments": [attachment]}) for to in s.recipients]
    s.last_sent_at = datetime.now(timezone.utc)
    await db.commit()
    return {"sent": sum(1 for r in results if r.get("status") == "sent"), "failed": [r.get("error_message") for r in results if r.get("status") == "failed"],
            "console": sum(1 for r in results if r.get("status") == "sent" and r.get("delivery_mode") == "console")}


async def send_due(session_factory, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    async with session_factory() as db:
        due = [s.id for s in (await db.execute(select(ReportSchedule).where(ReportSchedule.active.is_(True)))).scalars()
               if s.last_sent_at is None or now - _aware(s.last_sent_at) >= timedelta(days=FREQUENCIES[s.frequency])]
    sent = 0
    for sid in due:
        async with session_factory() as db:
            s = await db.get(ReportSchedule, sid)
            s.last_sent_at = now  # first, so a slow send isn't repeated by the next pass
            await db.commit()
            await send(db, s)
            sent += 1
    return sent


async def get(db: AsyncSession, tenant_id: str, schedule_id: uuid.UUID) -> ReportSchedule:
    s = await db.get(ReportSchedule, schedule_id)
    if s is None or s.tenant_id != tenant_id:
        raise LookupError("report not found")
    return s
