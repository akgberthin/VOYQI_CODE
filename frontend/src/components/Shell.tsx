"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { get, onExpired, post } from "@/lib/api";
import { SessionCtx, type Me } from "@/lib/session";
import { ToastHost } from "@/lib/ui";
import { Login } from "./Login";
import { NavLink } from "./NavLink";

type Console = "admin" | "marketing";
type NavGroup = { title: string; links: [href: string, label: string][] };

const CONSOLES: Record<Console, { title: string; home: string; nav: NavGroup[] }> = {
  admin: {
    title: "Super Admin",
    home: "/admin",
    nav: [
      { title: "Platform", links: [["/admin", "Overview"], ["/admin/services", "Services & APIs"], ["/admin/integrations", "Integrations"], ["/admin/messaging", "Messaging & automation"]] },
      { title: "AI orchestration", links: [["/admin/ai/agents", "Agents"], ["/admin/ai/approvals", "Approvals"], ["/admin/ai/tiers", "Autonomy tiers"], ["/admin/ai/prompts", "Prompts"], ["/admin/ai/goals", "Goals"], ["/admin/ai/decisions", "Decision log"]] },
      { title: "Access", links: [["/admin/access/tenants", "Tenants"], ["/admin/access/users", "Users & roles"], ["/admin/access/roles", "Roles"], ["/admin/access/overrides", "Permission overrides"], ["/admin/access/abac", "ABAC policies"], ["/admin/access/modules", "Modules & panels"]] },
      { title: "Website & content", links: [["/admin/website/pages", "Pages"], ["/admin/website/builder", "Website builder"], ["/admin/website/theme", "Colours & theme"], ["/admin/website/media", "Media library"], ["/admin/website/ai-assist", "AI assist"]] },
      { title: "Security & settings", links: [["/admin/security/sessions", "Privileged sessions"], ["/admin/security/audit", "Audit log"], ["/admin/settings", "Platform settings"], ["/admin/profile", "My profile"]] },
    ],
  },
  marketing: {
    title: "Marketing",
    home: "/marketing",
    nav: [
      { title: "Marketing", links: [["/marketing", "Command center"], ["/marketing/campaigns", "Campaigns"], ["/marketing/review", "Content review"], ["/marketing/calendar", "Calendar"], ["/marketing/automation", "Automation flows"]] },
      { title: "Channels", links: [["/marketing/compose/email", "✉ Email"], ["/marketing/compose/sms", "💬 SMS"], ["/marketing/compose/push", "🔔 Push"], ["/marketing/compose/voice", "📞 Voice"], ["/marketing/social", "◎ Social"], ["/marketing/autopilot", "◎ Social autopilot"]] },
      { title: "Create", links: [["/marketing/studio", "✦ AI studio"], ["/marketing/library", "Library"]] },
      { title: "Audience & results", links: [["/marketing/audience", "Audience & suppression"], ["/marketing/ab-tests", "A/B tests"], ["/marketing/analytics", "Analytics"], ["/marketing/reports", "Reports"], ["/marketing/leads", "Leads"]] },
      { title: "SEO & GEO growth", links: [["/marketing/seo", "Growth overview"], ["/marketing/seo/strategy", "Strategy & autopilot"], ["/marketing/seo/research", "Research & keywords"], ["/marketing/seo/content", "Content & programmatic"], ["/marketing/seo/technical", "Technical SEO"], ["/marketing/seo/geo", "✦ AI search (GEO)"], ["/marketing/seo/links", "Links & authority"], ["/marketing/seo/cro", "Conversion (CRO)"], ["/marketing/seo/local", "Local SEO"], ["/marketing/seo/reports", "Reports & activity"], ["/marketing/seo/setup", "Integrations & agents"]] },
    ],
  },
};

/** The nav item that shows a "waiting for you" count, and how to get it. */
const BADGE: Record<Console, [href: string, load: () => Promise<number>]> = {
  admin: ["/admin/ai/approvals", async () => { const r = await get("/api/v1/ai/recommendations?status=pending"); return r.ok ? r.data.length : 0; }],
  marketing: ["/marketing/review", async () => { const r = await get("/api/v1/marketing/content?status=held_for_review"); return r.ok ? r.data.length : 0; }],
};

const ME_KEY = "voyqi.me";
function loadMe(): Me | null {
  try {
    const me = JSON.parse(sessionStorage.getItem(ME_KEY) || "null");
    return me?.access ? me : null;
  } catch { return null; }
}

const consoleOf = (path: string): Console | null => (path.startsWith("/admin") ? "admin" : path.startsWith("/marketing") ? "marketing" : null);

/** Sign-in, topbar and sidebar for every console page. */
export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname() || "/";
  const router = useRouter();
  // The session lives in sessionStorage, which only the browser has, so
  // nothing renders until it has been read (no server/client mismatch).
  const [ready, setReady] = useState(false);
  const [me, setMe] = useState<Me | null>(null);
  const [badge, setBadge] = useState(0);
  const remember = (m: Me | null) => {
    setMe(m);
    try { m ? sessionStorage.setItem(ME_KEY, JSON.stringify(m)) : sessionStorage.removeItem(ME_KEY); } catch { /* private mode */ }
  };
  useEffect(() => { setMe(loadMe()); setReady(true); }, []);
  useEffect(() => onExpired(() => remember(null)), []);

  const current = consoleOf(path);
  const allowed = (c: Console | null): c is Console => !!c && !!me?.access[c];
  const fallback: Console | null = me ? (me.access.admin ? "admin" : me.access.marketing ? "marketing" : null) : null;

  // Pages outside a console, or in one this account can't use, go to the
  // console it can use.
  useEffect(() => {
    if (me && !allowed(current) && fallback) router.replace(CONSOLES[fallback].home);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, current]);

  useEffect(() => {
    if (!me || !allowed(current)) return;
    BADGE[current][1]().then(setBadge).catch(() => setBadge(0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me, path]);

  const logout = async () => { await post("/api/v1/auth/logout"); remember(null); };

  if (!ready) return null;
  if (!me) return <ToastHost><Login onLogin={remember} /></ToastHost>;
  if (!allowed(current)) return null;

  const cfg = CONSOLES[current];
  const initials = me.email.split("@")[0].split(/[._-]/).map((p) => p[0]?.toUpperCase()).join("").slice(0, 2);
  const both = me.access.admin && me.access.marketing;
  return (
    <SessionCtx.Provider value={{ me, logout }}>
      <ToastHost>
        <div className="shell">
          <header className="topbar">
            <span className="brand">VOYQI</span>
            {both ? (
              <span className="switch">
                {(Object.keys(CONSOLES) as Console[]).map((c) => (
                  <Link key={c} href={CONSOLES[c].home} className={c === current ? "on" : ""}>{CONSOLES[c].title}</Link>
                ))}
              </span>
            ) : <span className="where">{cfg.title}</span>}
            <span className="where">tenant {me.tenant_id}</span>
            <span className="spacer" />
            <div className="who">
              {me.access.admin
                ? <Link href="/admin/profile" className="row" style={{ color: "inherit", textDecoration: "none" }}><span className="avatar">{initials}</span><span>{me.email}</span></Link>
                : <><span className="avatar">{initials}</span><span>{me.email}</span></>}
              <button className="sm ghost" onClick={logout}>Sign out</button>
            </div>
          </header>
          <nav className="side">
            {cfg.nav.map((g) => (
              <div key={g.title}>
                <h6>{g.title}</h6>
                {g.links.map(([href, label]) => (
                  <NavLink key={href} href={href} exact={href === cfg.home}>
                    {label}{href === BADGE[current][0] && badge > 0 && <span className="count">{badge}</span>}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
          <main className="main">{children}</main>
        </div>
      </ToastHost>
    </SessionCtx.Provider>
  );
}
