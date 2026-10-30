"""Who sent a message, from the source tag every sender records.

Senders tag messages (stored in Notification.template_key):
  journey:<id>                automation flow (triggered by events / AI signals)
  marketing:<send_id>         a campaign send a person started
  marketing:<send_id>:retry   automatic voice retry
  marketing:report[:<id>]     scheduled / emailed marketing report
  seo_outreach                SEO agent link outreach
  super_admin_test            test message from the super admin console
  anything else               platform templates (sign-in, password reset, …)
"""

SENDERS = ("automation", "campaign", "retry", "report", "seo_agent", "super_admin", "system")
LABEL = {"automation": "Automation flow", "campaign": "Campaign", "retry": "Automatic retry", "report": "Scheduled report",
         "seo_agent": "SEO agent", "super_admin": "Super admin", "system": "Platform (sign-in, account)"}
# Sent without a person pressing send at that moment.
AUTOMATED = {"automation", "retry", "report", "seo_agent", "system"}


def classify(tag: str | None) -> str:
    t = tag or ""
    if t.startswith("journey:"):
        return "automation"
    if t.startswith("marketing:report"):
        return "report"
    if t.startswith("marketing:"):
        return "retry" if t.endswith(":retry") else "campaign"
    if t.startswith("seo_"):
        return "seo_agent"
    if t == "super_admin_test":
        return "super_admin"
    return "system"
