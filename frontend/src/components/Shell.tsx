"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { loadAlerts, readIds } from "@/features/admin/alerts";
import { loadVoyqiUi, pickerGroups, type Theme } from "@/lib/voyqiUi";
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
      { title: "Platform", links: [["/admin", "Overview"], ["/admin/notifications", "Notifications"], ["/admin/services", "Services & APIs"], ["/admin/integrations", "Integrations"], ["/admin/messaging", "Messaging & automation"]] },
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

/** Pages where an AI agent does the work. */
const AI_TAG = new Set(["/admin/ai/agents", "/admin/website/ai-assist", "/marketing/studio", "/marketing/autopilot", "/marketing/seo/geo", "/marketing/seo/strategy"]);
const strip = (label: string) => label.replace(/^[^\p{L}\p{N}]+/u, "");

/** Theme preference, kept by the shared library (falls back to plain storage if it didn't load). */
function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>("dark");
  useEffect(() => {
    try { const t = localStorage.getItem("voyqi.theme") as Theme | null; if (t) setTheme(t); } catch { /* default */ }
    let off = () => {};
    loadVoyqiUi().then((lib) => { if (lib) { setTheme(lib.getThemePref()); off = lib.subscribeTheme(() => setTheme(lib.getThemePref())); } });
    return () => off();
  }, []);
  return [theme, (t) => {
    setTheme(t);
    if (window.VoyqiUI) window.VoyqiUI.setThemePref(t);
    else {
      document.documentElement.dataset.theme = t;
      try { localStorage.setItem("voyqi.theme", t); } catch { /* not remembered */ }
    }
  }];
}

/** Language picker, as in the Marketing Command Center: active languages by region, native names. */
function LanguagePicker() {
  const [lib, setLib] = useState<NonNullable<Window["VoyqiUI"]> | null>(null);
  const [code, setCode] = useState("en");
  useEffect(() => {
    let off = () => {};
    loadVoyqiUi().then((l) => { if (!l) return; setLib(l); setCode(l.getLocale()); off = l.subscribe(() => setCode(l.getLocale())); });
    return () => off();
  }, []);
  if (!lib) return null;
  return (
    <select className="vp-lang" aria-label="Choose language" data-no-i18n="" value={code} onChange={(e) => lib.setLocale(e.target.value)}>
      {pickerGroups(lib).map(([region, ls]) => (
        <optgroup key={region} label={region}>{ls.map((l) => <option key={l.code} value={l.code} lang={l.code}>{l.native}</option>)}</optgroup>
      ))}
    </select>
  );
}

/** Jump to any page of the consoles this account can open. Ctrl+K or / to focus. */
function Search({ consoles }: { consoles: Console[] }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(0);
  const box = useRef<HTMLInputElement>(null);
  const all = useMemo(() => consoles.flatMap((c) => CONSOLES[c].nav.flatMap((g) => g.links.map(([href, label]) => ({ href, label: strip(label), where: `${CONSOLES[c].title} · ${g.title}` })))), [consoles]);
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hits = words.length ? all.filter((x) => words.every((w) => `${x.label} ${x.where}`.toLowerCase().includes(w))).slice(0, 12) : [];
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target as HTMLElement)?.tagName || "");
      if ((e.key === "k" && (e.ctrlKey || e.metaKey)) || (e.key === "/" && !typing)) { e.preventDefault(); box.current?.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const go = (href: string) => { setQ(""); setOpen(false); box.current?.blur(); router.push(href); };
  return (
    <div className="search">
      <input ref={box} placeholder="Search pages…  (Ctrl+K)" value={q} aria-label="Search pages"
        onChange={(e) => { setQ(e.target.value); setPos(0); setOpen(true); }} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setPos(Math.min(pos + 1, hits.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setPos(Math.max(pos - 1, 0)); }
          if (e.key === "Enter" && hits[pos]) go(hits[pos].href);
          if (e.key === "Escape") { setQ(""); box.current?.blur(); }
        }} />
      {open && words.length > 0 && (
        <div className="results">
          {hits.length ? hits.map((h, i) => (
            <a key={h.href} href={h.href} className={i === pos ? "on" : ""} onMouseDown={(e) => { e.preventDefault(); go(h.href); }}><span>{h.label}</span><span className="muted">{h.where}</span></a>
          )) : <div className="muted" style={{ padding: 10 }}>No page matches “{q}”.</div>}
        </div>
      )}
    </div>
  );
}

/** Unread operator notifications, refreshed on navigation and every 2 minutes. */
function Bell({ me, path }: { me: Me; path: string }) {
  const [ids, setIds] = useState<string[]>([]);
  const [read, setRead] = useState<Set<string>>(new Set());
  useEffect(() => {
    let live = true;
    const load = () => loadAlerts(me.access.marketing).then((r) => live && setIds(r.alerts.map((a) => a.id))).catch(() => {});
    load();
    const t = setInterval(load, 120000);
    return () => { live = false; clearInterval(t); };
  }, [me, path]);
  useEffect(() => {
    const sync = () => setRead(readIds(me.user_id));
    sync();
    window.addEventListener("voyqi-alerts-read", sync);
    return () => window.removeEventListener("voyqi-alerts-read", sync);
  }, [me]);
  const unread = ids.filter((i) => !read.has(i)).length;
  return (
    <Link href="/admin/notifications" className="bell" title={unread ? `${unread} unread notification${unread > 1 ? "s" : ""}` : "Notifications"} aria-label="Notifications">
      🔔{unread > 0 && <span className="dot">{unread > 99 ? "99+" : unread}</span>}
    </Link>
  );
}

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
  const [theme, setTheme] = useTheme();
  const [brand, setBrand] = useState("VOYQI");
  useEffect(() => {
    if (!me) return;
    const load = () => get("/api/v1/rbac/settings?category=general").then((r) => {
      const name = r.ok ? (r.data as any[]).find((x) => x.key === "platform")?.value?.platform_name : null;
      if (name) setBrand(name);
    }).catch(() => {});
    load();
    window.addEventListener("voyqi-platform-changed", load);
    return () => window.removeEventListener("voyqi-platform-changed", load);
  }, [me]);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  useEffect(() => { try { setClosed(new Set(JSON.parse(localStorage.getItem("voyqi.nav.closed") || "[]"))); } catch { /* all open */ } }, []);
  const toggleGroup = (title: string) => {
    const next = new Set(closed);
    if (next.has(title)) next.delete(title); else next.add(title);
    setClosed(next);
    try { localStorage.setItem("voyqi.nav.closed", JSON.stringify([...next])); } catch { /* not remembered */ }
  };
  const remember = (m: Me | null) => {
    setMe(m);
    try { m ? sessionStorage.setItem(ME_KEY, JSON.stringify(m)) : sessionStorage.removeItem(ME_KEY); } catch { /* private mode */ }
  };
  useEffect(() => { setMe(loadMe()); setReady(true); loadVoyqiUi(); }, []);
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
            <span className="brand">{brand}</span>
            {both ? (
              <span className="switch">
                {(Object.keys(CONSOLES) as Console[]).map((c) => (
                  <Link key={c} href={CONSOLES[c].home} className={c === current ? "on" : ""}>{CONSOLES[c].title}</Link>
                ))}
              </span>
            ) : <span className="where">{cfg.title}</span>}
            <span className="where tenant">tenant {me.tenant_id}</span>
            <Search consoles={(Object.keys(CONSOLES) as Console[]).filter((c) => me.access[c])} />
            <span className="spacer" />
            <LanguagePicker />
            <div className="theme-pick" role="group" aria-label="Theme">
              {(["light", "dark", "system"] as Theme[]).map((t) => <button key={t} className={theme === t ? "on" : ""} onClick={() => setTheme(t)}>{t === "light" ? "☀ Light" : t === "dark" ? "☾ Dark" : "System"}</button>)}
            </div>
            {me.access.admin && <Bell me={me} path={path} />}
            <div className="who">
              {me.access.admin
                ? <Link href="/admin/profile" className="row" style={{ color: "inherit", textDecoration: "none" }}><span className="avatar">{initials}</span><span className="email">{me.email}</span></Link>
                : <><span className="avatar">{initials}</span><span className="email">{me.email}</span></>}
              <button className="sm ghost" onClick={logout}>Sign out</button>
            </div>
          </header>
          <nav className="side">
            {cfg.nav.map((g) => {
              // A group holding the current page always stays open.
              const isOpen = !closed.has(g.title) || g.links.some(([href]) => href === path || (href !== cfg.home && path.startsWith(href + "/")));
              return (
                <div key={g.title}>
                  <h6 className="toggle" onClick={() => toggleGroup(g.title)} aria-expanded={isOpen}>{g.title}<span>{isOpen ? "▾" : "▸"}</span></h6>
                  {isOpen && g.links.map(([href, label]) => (
                    <NavLink key={href} href={href} exact={href === cfg.home}>
                      {label}
                      {href === BADGE[current][0] && badge > 0 ? <span className="count">{badge}</span> : AI_TAG.has(href) && <span className="tag">AI</span>}
                    </NavLink>
                  ))}
                </div>
              );
            })}
          </nav>
          <main className="main">{children}</main>
        </div>
      </ToastHost>
    </SessionCtx.Provider>
  );
}
