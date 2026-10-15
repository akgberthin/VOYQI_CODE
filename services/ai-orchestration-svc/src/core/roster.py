"""The 12-agent roster from docs/AI_ORCHESTRATION_SCALING.md mapped onto
the code that implements each one.

Roster agents are judgment domains; in code some are one module and some
span several (CRM work, for instance, lives in revenue_agent and
concierge_agent). `policy_agent` is the tier-policy namespace, `agent_keys`
the DecisionLog/Agent keys that act for it, and `status` an honest
account of what exists — "partial" lists what is still missing.
"""
ROSTER: list[dict] = [
    {
        "name": "Supervisor", "tier": "realtime", "policy_agent": None, "agent_keys": [],
        "status": "implemented", "implemented_by": ["services/supervisor.py", "core/kafka_consumer.py"],
        "covers": "Routes every platform event to the agent that owns it.",
    },
    {
        "name": "Concierge", "tier": "realtime", "policy_agent": "concierge", "agent_keys": [],
        "status": "missing", "implemented_by": [],
        "covers": "Member journey logistics (routine logistics, schedule changes, premium reroutes).",
        "missing": "No journey-logistics agent: subscription-svc's ConciergeJourney has no itinerary fields to act on. Needs the MOB-010 spec from the PRD.",
    },
    {
        "name": "Member Experience", "tier": "realtime", "policy_agent": "member-experience", "agent_keys": ["member_experience_agent"],
        "status": "implemented", "implemented_by": ["services/member_experience_agent.py"],
        "covers": "Upgrade-pattern detection and upgrade offers.",
    },
    {
        "name": "Relationship Intelligence", "tier": "realtime", "policy_agent": "relationship-intelligence",
        "agent_keys": ["concierge_agent", "revenue_agent"], "status": "implemented",
        "implemented_by": ["services/concierge_agent.py (CRM-003 inbox)", "services/revenue_agent.py (CRM-001/002/007)"],
        "covers": "Customer 360 identity resolution, lead scoring and routing, inbox triage and reply drafting.",
    },
    {
        "name": "Fleet Intelligence", "tier": "batch", "policy_agent": "fleet-intelligence", "agent_keys": ["fleet_intelligence_agent"],
        "status": "implemented", "implemented_by": ["services/fleet_intelligence_agent.py"],
        "covers": "Staging, maintenance, telematics, incidents, disposition, financing, EV and fuel (FLT-001..013).",
    },
    {
        "name": "Pricing Intelligence", "tier": "batch", "policy_agent": "pricing-intelligence", "agent_keys": ["pricing_intelligence_agent"],
        "status": "implemented", "implemented_by": ["services/pricing_intelligence_agent.py"],
        "covers": "Rental rates, trade-in valuation, retail pricing, auction max bids.",
    },
    {
        "name": "Revenue", "tier": "batch", "policy_agent": "revenue", "agent_keys": ["fleet_intelligence_agent"],
        "status": "partial", "implemented_by": ["services/fleet_intelligence_agent.py (vehicle.idle.48h channel recommendation)"],
        "covers": "Idle-inventory channel recommendations and term-conversion offers.",
        "missing": "Term-conversion offers (rental-to-subscription) are not implemented — no event or service endpoint defines them yet.",
    },
    {
        "name": "Retention", "tier": "batch", "policy_agent": "retention", "agent_keys": ["member_experience_agent"],
        "status": "implemented", "implemented_by": ["services/member_experience_agent.py (churn risk, retention bonus, win-back)"],
        "covers": "Churn-risk sweeps, retention-save bonuses, cancellation win-back offers.",
    },
    {
        "name": "Sales & Originations Intelligence", "tier": "batch", "policy_agent": "sales-originations-intelligence",
        "agent_keys": ["dealer_intelligence_agent", "revenue_agent"], "status": "implemented",
        "implemented_by": ["services/dealer_intelligence_agent.py", "services/revenue_agent.py (ORG-001..003)"],
        "covers": "Deal desk, credit decisioning (tier 3), pre-qualification, risk scoring, lender matching.",
    },
    {
        "name": "Talent & Workforce Intelligence", "tier": "batch", "policy_agent": "talent-workforce-intelligence",
        "agent_keys": ["onboarding_agent", "benefits_agent", "recruiting_agent", "performance_agent", "workforce_analytics_agent",
                       "academy_agent", "case_management_agent", "communication_agent"],
        "status": "implemented", "implemented_by": ["services/talent_agent.py (HRM-001..008)"],
        "covers": "Onboarding, benefits, recruiting, performance, attrition, academy, HR cases, communications.",
    },
    {
        "name": "Compliance & Risk Intelligence", "tier": "batch", "policy_agent": "compliance-risk-intelligence",
        "agent_keys": ["compliance_audit_agent"], "status": "implemented", "implemented_by": ["services/compliance_audit_agent.py"],
        "covers": "Access, privileged-session, content, DMS, fair-lending and fleet-security audit; review cards with the right tier.",
    },
    {
        "name": "Portfolio Intelligence", "tier": "batch", "policy_agent": "portfolio-intelligence",
        "agent_keys": ["portfolio_intelligence_agent"], "status": "implemented", "implemented_by": ["services/portfolio_intelligence_agent.py"],
        "covers": "KPI alert assessment and cross-domain reports over executive, management, real-estate and capital-finance.",
    },
]
