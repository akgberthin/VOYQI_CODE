"""Task requests sent by email. Only senders the site authorises are
heard; a request that reads like instructions to an AI becomes tasks
that wait for a person's approval."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.seo import rules, tasks
from src.seo.common import activity, conflict, emit, get_owned, get_site, invalid, row
from src.seo.models import SeoSite, SeoTaskRequest
from src.seo.sites import EMAIL_RE

TOPIC_TYPES = {"content_create": "keyword", "content_refresh": "keyword", "keyword_research": "keyword", "geo_check": "keyword",
               "cro_analysis": "topic"}
URL_TYPES = {"content_refresh", "cro_analysis", "technical_audit", "schema_implementation", "redirect_fix"}


async def receive(db: AsyncSession, site: SeoSite, body: dict) -> SeoTaskRequest:
    sender = str(body.get("sender") or "").strip().lower()
    if "<" in sender and ">" in sender:
        sender = sender[sender.index("<") + 1: sender.index(">")]
    if not EMAIL_RE.match(sender):
        raise invalid("sender must be an email address")
    request = SeoTaskRequest(tenant_id=site.tenant_id, site_id=site.id, sender=sender, subject=str(body.get("subject") or "")[:300],
                             body=str(body.get("body") or "")[:20000], status="received")
    db.add(request)
    await db.flush()
    await activity(db, site, "task", f"Task request received from {sender}", sender, "task_request", request.id)
    await db.commit()
    await emit("seo.email_received", site, {"request_id": request.id, "sender": sender})
    return request


async def convert(db: AsyncSession, tenant_id: str, request_id: str, actor: str) -> SeoTaskRequest:
    """Turns a received request into tasks (the Task Intake Agent's job)."""
    request = await get_owned(db, SeoTaskRequest, tenant_id, request_id, "request")
    if request.status != "received":
        raise conflict(f"request is already {request.status.replace('_', ' ')}")
    site = await get_site(db, tenant_id, request.site_id)
    sender = request.sender
    if sender not in [a.lower() for a in site.authorized_requesters or []]:
        request.status, request.parsed = "rejected_sender", {"reason": f"{sender} is not an authorized requester for this site"}
        await activity(db, site, "task", f"Task request from {sender} rejected: not an authorized requester", actor, "task_request", request.id)
        await db.commit()
        return request
    request.injection_suspected = rules.looks_like_injection(f"{request.subject}\n{request.body}")
    parsed = rules.parse_request(request.subject, request.body, site.domain)
    if not parsed["intents"]:
        request.status = "needs_clarification"
        request.parsed = {**parsed, "reply": "I could not tell what to do. I can: " + ", ".join(rules.REQUESTABLE) + "."}
    else:
        ids = []
        for intent in parsed["intents"]:
            params = {"requested_by": sender}
            if parsed["topic"] and intent["type"] in TOPIC_TYPES:
                params[TOPIC_TYPES[intent["type"]]] = parsed["topic"].lower()
            if parsed["urls"] and intent["type"] in URL_TYPES:
                params["url"] = parsed["urls"][0]
            task, _new = await tasks.create_task(db, site, {
                "type": intent["type"], "title": f"{intent['label']}: requested by {sender}", "description": request.subject,
                "params": params, "impact": 70, "effort": 3, "confidence": 0.8, "source": "email",
                "injection": request.injection_suspected}, actor, commit=False)
            ids.append(str(task.id))
        request.status, request.task_ids, request.parsed = "converted", ids, parsed
    await activity(db, site, "task", f"Task request from {sender}: {request.status.replace('_', ' ')} ({len(request.task_ids)} task(s))"
                   + (" — flagged as instruction-like" if request.injection_suspected else ""), actor, "task_request", request.id)
    await db.commit()
    return request


async def list_requests(db: AsyncSession, tenant_id: str, site_id: str) -> list[dict]:
    site = await get_site(db, tenant_id, site_id)
    return [row(r) for r in (await db.execute(select(SeoTaskRequest).where(SeoTaskRequest.site_id == site.id)
                                              .order_by(SeoTaskRequest.received_at.desc()).limit(100))).scalars()]
