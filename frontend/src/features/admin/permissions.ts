/* What the gateway checks. Every route prefix /api/v1/<module> is a
   module; reading needs <module>.read, writing <module>.write, <module>.*
   grants both, <module>.self only the caller's own records, * everything.
   "platform" guards the super admin's platform API (services, OpenAPI). */

export const MODULES = [
  "platform", "ai", "rbac", "identity", "user", "notifications", "marketing", "crm", "subscription", "payment",
  "fleet", "rental", "hub", "pricing", "dms", "sales", "originations", "org", "hrm", "executive", "management",
  "real-estate", "capital-finance", "insurance-risk", "ip", "security-compliance",
] as const;

export const PERMISSION_KEYS = ["*", ...MODULES.flatMap((m) => [`${m}.*`, `${m}.read`, `${m}.write`]), "subscription.self", "payment.self", "user.self"];

/** Same rule as the gateway's authorization.is_allowed. */
export function allows(granted: string[], required: string): boolean {
  const module = required.split(".", 1)[0];
  return granted.includes(required) || granted.includes(`${module}.*`) || granted.includes("*");
}

/** Consoles and their sub-sections, for module & panel grants. */
export const CONSOLES: { key: string; label: string; sections: [string, string][] }[] = [
  { key: "fleet", label: "Fleet Management", sections: [["fleet.inventory", "Inventory & intake"], ["fleet.staging", "Staging & hub board"], ["fleet.maintenance", "Maintenance"], ["fleet.incidents", "Incidents & claims"], ["fleet.telematics", "Telematics"], ["fleet.safety", "Driving safety"], ["fleet.lifecycle", "Depreciation & disposition"], ["fleet.floorplan", "Floorplan financing"], ["fleet.fuel", "Fuel & energy cost"], ["fleet.ev_charging", "EV charging & battery"], ["fleet.analytics", "Utilization, TCO & AI optimizer"]] },
  { key: "mobility", label: "Mobility Admin", sections: [["mobility.members", "Members & KYC"], ["mobility.enrollment", "Plans & enrollment"], ["mobility.lifecycle", "Subscription lifecycle"], ["mobility.billing", "Billing & invoicing"], ["mobility.gu", "Growth Units economy"], ["mobility.swap", "Vehicle swap"], ["mobility.travel", "Travel & continuity"], ["mobility.black", "VOYQI Black & concierge"]] },
  { key: "dms", label: "Dealer Center", sections: [["dms.rooftops", "Rooftop directory"], ["dms.inventory", "Retail inventory"], ["dms.deals", "Sales desk & deals"], ["dms.fi", "F&I & compliance"], ["dms.lending", "Lender & credit apps"], ["dms.appraisal", "Trade-in appraisal"], ["dms.service", "Service & repair orders"], ["dms.recon", "Recon workflow"], ["dms.parts", "Parts inventory"], ["dms.loaners", "Loaner & courtesy"], ["dms.pl", "P&L & KPI board"], ["dms.franchise", "Franchise compliance"]] },
  { key: "rental", label: "Rental", sections: [["rental.fleet", "Rental fleet"], ["rental.reservations", "Reservations"], ["rental.pricing", "Dynamic pricing"], ["rental.demand", "Demand & utilization"], ["rental.insights", "Insights"], ["rental.reallocation", "Reallocation"]] },
  { key: "hub", label: "Hub Network", sections: [["hub.directory", "Hub directory & map"], ["hub.operations", "Operations board"], ["hub.security", "Card security"], ["hub.analytics", "Capacity & utilization"]] },
  { key: "marketing", label: "Marketing", sections: [["marketing.campaigns", "Campaigns & channels"], ["marketing.audience", "Audience & segments"], ["marketing.automation", "Automation flows"], ["marketing.ai_studio", "AI studio"], ["marketing.analytics", "Analytics & reports"], ["marketing.seo", "SEO & GEO growth"]] },
  { key: "hrm", label: "HRM", sections: [["hrm.people", "People directory"], ["hrm.recruiting", "Recruiting"], ["hrm.academy", "Academy"], ["hrm.notices", "Notices & communication"]] },
  { key: "crm", label: "CRM", sections: [["crm.leads", "Leads"], ["crm.contacts", "Contacts"]] },
];
