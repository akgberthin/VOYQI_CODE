"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { api, get, must, post } from "@/lib/api";
import { Badge, Empty, ErrorNote, Loading, PageHead, useAction, useLoad } from "@/lib/ui";

/* Growth / SEO: typed access to marketing-svc's /seo API, the site
   switcher every Growth page shares, and small helpers. */

const S = "/api/v1/marketing/seo";
export const seo = {
  get: <T = any>(path: string) => must<T>(get(`${S}${path}`)),
  post: <T = any>(path: string, body: unknown = {}) => must<T>(post(`${S}${path}`, body)),
  put: <T = any>(path: string, body: unknown = {}) => must<T>(api("PUT", `${S}${path}`, body)),
  patch: <T = any>(path: string, body: unknown = {}) => must<T>(api("PATCH", `${S}${path}`, body)),
  del: <T = any>(path: string) => must<T>(api("DELETE", `${S}${path}`)),
};

export type Site = {
  id: string; name: string; domain: string; autopilot_mode: "off" | "assisted" | "autopilot"; caps: Record<string, number>;
  autonomy_overrides: Record<string, string>; report_frequency: string; report_recipients: string[]; authorized_requesters: string[];
  business_profile: Record<string, any>; last_cycle_at: string | null;
};

const SITE_KEY = "voyqi.seo.site";
const MODE_TEXT: Record<string, string> = {
  off: "Off — agents do nothing on their own",
  assisted: "Assisted — analysis runs unattended; changes to the site and messages wait for a person",
  autopilot: "Autopilot — runs what the plan allows, within the caps; hard gates still hold",
};

export const fmtN = (n: number | null | undefined) => (n === null || n === undefined ? "—" : Number(n).toLocaleString());
export const fmtPct = (n: number | null | undefined, digits = 1) => (n === null || n === undefined ? "—" : `${(n * 100).toFixed(digits)}%`);
export const signed = (n: number | null | undefined, unit = "%") => (n === null || n === undefined ? "—" : `${n > 0 ? "+" : ""}${n}${unit}`);
export const sev = (s: string) => (s === "critical" || s === "high" ? "bad" : s === "warning" || s === "medium" ? "warn" : "info");

/** Every Growth page: the site switcher, then the page for that site. */
export function SeoPage({ title, text, actions, bare, children }: {
  title: string; text?: ReactNode; actions?: (site: Site, reload: () => void) => ReactNode; bare?: boolean; children: (site: Site, reload: () => void) => ReactNode;
}) {
  const run = useAction();
  const sites = useLoad(() => seo.get<Site[]>("/sites"));
  const [id, setId] = useState<string>("");
  useEffect(() => {
    if (!sites.data?.length) return;
    let stored = "";
    try { stored = localStorage.getItem(SITE_KEY) || ""; } catch { /* private mode */ }
    setId((cur) => (sites.data!.some((s) => s.id === cur) ? cur : sites.data!.some((s) => s.id === stored) ? stored : sites.data![0].id));
  }, [sites.data]);
  const choose = (v: string) => { setId(v); try { localStorage.setItem(SITE_KEY, v); } catch { /* private mode */ } };
  const site = (sites.data || []).find((s) => s.id === id);
  const reload = () => { sites.reload(); };
  const [cycling, setCycling] = useState(false);
  const cycle = async () => { setCycling(true); await run("Autopilot cycle finished", () => seo.post(`/sites/${site!.id}/cycle`)); setCycling(false); reload(); };
  const setMode = (mode: string) => run(`Mode: ${mode}`, () => seo.patch(`/sites/${site!.id}`, { autopilot_mode: mode })).then(reload);

  return (
    <>
      {!bare && <PageHead title={title} text={text}>{site && actions?.(site, reload)}</PageHead>}
      <ErrorNote error={sites.error} />
      {sites.loading && !sites.data ? <Loading /> : !(sites.data || []).length ? (
        <Empty>No site yet. Add your website under <Link href="/marketing/seo/setup">Integrations & agents</Link> to start.</Empty>
      ) : site && (
        <>
          <div className="card mb seo-bar">
            <select value={site.id} onChange={(e) => choose(e.target.value)} title="Site">
              {(sites.data || []).map((s) => <option key={s.id} value={s.id}>{s.name} · {s.domain}</option>)}
            </select>
            <div className="seg" title={MODE_TEXT[site.autopilot_mode]}>
              {(["off", "assisted", "autopilot"] as const).map((m) => <button key={m} className={site.autopilot_mode === m ? "on" : ""} onClick={() => site.autopilot_mode !== m && setMode(m)}>{m}</button>)}
            </div>
            <span className="muted" style={{ fontSize: 12, flex: 1 }}>{MODE_TEXT[site.autopilot_mode]}</span>
            <span className="muted" style={{ fontSize: 12 }}>{site.last_cycle_at ? `last cycle ${new Date(site.last_cycle_at).toLocaleString()}` : "no cycle yet"}</span>
            <button className="sm ghost" disabled={cycling || site.autopilot_mode === "off"} onClick={cycle}>{cycling ? "Running…" : "Run cycle now"}</button>
          </div>
          {children(site, reload)}
        </>
      )}
    </>
  );
}

/** Shown where a number needs a connection the site doesn't have yet. */
export function Missing({ text }: { text?: string | null }) {
  return text ? <div className="muted" style={{ fontSize: 12 }}>Needs data: {text} — <Link href="/marketing/seo/setup">set up</Link></div> : null;
}

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return <div className="card kpi"><div className="l">{label}</div><div className="n" style={tone ? { color: `var(--${tone})` } : undefined}>{value}</div>{sub !== undefined && <div className="d">{sub}</div>}</div>;
}

/** A small line chart (SVG) — enough to show a trend without a library. */
export function LineChart({ points, height = 140, label }: { points: { x: string; y: number }[]; height?: number; label?: string }) {
  if (points.length < 2) return <div className="empty">Not enough data for a trend yet.</div>;
  const w = 600, pad = 6, max = Math.max(...points.map((p) => p.y)) || 1, min = Math.min(...points.map((p) => p.y), 0);
  const xy = points.map((p, i) => [pad + (i * (w - 2 * pad)) / (points.length - 1), height - pad - ((p.y - min) / (max - min || 1)) * (height - 2 * pad)]);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${height}`} style={{ width: "100%", height }} role="img" aria-label={label}>
      <path d={`${line} L${xy.at(-1)![0]},${height - pad} L${xy[0][0]},${height - pad} Z`} fill="var(--gold)" opacity=".12" />
      <path d={line} fill="none" stroke="var(--gold)" strokeWidth="2" />
      {xy.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="2.5" fill="var(--gold)"><title>{`${points[i].x}: ${fmtN(points[i].y)}`}</title></circle>)}
    </svg>
  );
}

/** Horizontal bars for a few labelled values. */
export function Bars({ rows, format = fmtN }: { rows: [string, number][]; format?: (n: number) => string }) {
  const max = Math.max(1, ...rows.map(([, v]) => v));
  return (
    <div className="grid" style={{ gap: 8 }}>{rows.map(([l, v]) => (
      <div key={l}><div className="row between" style={{ fontSize: 12.5 }}><span>{l}</span><span className="muted">{format(v)}</span></div><div className="bar" style={{ marginTop: 4 }}><div style={{ width: `${(v / max) * 100}%` }} /></div></div>
    ))}</div>
  );
}

export function Explain({ title, children }: { title: string; children: ReactNode }) {
  return <div className="notice info mb"><b>{title}</b> {children}</div>;
}

export const ModeBadge = ({ status }: { status: string }) => <Badge kind={status === "connected" ? "ok" : status === "configured" ? "warn" : status === "error" || status === "failed" ? "bad" : ""}>{status.replace(/_/g, " ")}</Badge>;
