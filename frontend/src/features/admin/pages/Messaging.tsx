"use client";

import { useState } from "react";
import Link from "next/link";
import { api, get, must, post } from "@/lib/api";
import { Badge, Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "@/lib/ui";

/* What each outward channel runs on, and whether that provider is live. */
const CHANNELS: [string, string, string][] = [
  ["AI reasoning", "anthropic", "Agents reason with Claude; otherwise rule-based fallback"],
  ["Email", "smtp", "Campaigns, journeys and system email"],
  ["SMS", "twilio", "Campaigns and journeys"],
  ["Voice calls", "twilio", "AI voice campaigns (consent + calling window enforced)"],
  ["Push", "fcm", "Mobile notifications"],
  ["Facebook & Instagram", "meta", "Social publishing"],
  ["LinkedIn", "linkedin", "Social publishing"],
  ["X", "x", "Social publishing"],
  ["Images", "openai", "AI studio and autopilot creatives"],
];

export function readiness(integrations: any[], provider: string): [string, string] {
  const it = integrations.find((i) => i.provider === provider);
  if (!it?.configured) return ["", "console only — not set up"];
  if (!it.enabled) return ["warn", "disabled"];
  if (it.test_status === "ok") return ["ok", "live"];
  if (it.test_status === "failed") return ["bad", "test failed"];
  return ["warn", "set up, not tested"];
}

/** A one-off message through notification-svc — the quickest way to see
    whether a channel's provider really delivers. */
function SendTest({ onSent }: { onSent: () => void }) {
  const run = useAction();
  const [f, setF] = useState({ channel: "email", recipient: "", subject: "VOYQI test message", body: "This is a test from the VOYQI super admin console." });
  const [result, setResult] = useState<any>(null);
  const send = () => run("Handed to notification-svc", async () => {
    setResult(await must(post("/api/v1/notifications/send-direct", { ...f, subject: f.channel === "email" ? f.subject : undefined, category: "transactional", source: "super_admin_test" })));
  }).then(onSent);
  return (
    <div className="card mb">
      <h3>Send a test message</h3>
      <div className="sub">Goes through the same provider every campaign and agent uses. Without a provider set up for the channel it is only logged (“console only”).</div>
      <div className="form mt">
        <label className="field">Channel<select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{["email", "sms", "push", "voice"].map((c) => <option key={c}>{c}</option>)}</select></label>
        <label className="field">{f.channel === "email" ? "Email" : f.channel === "push" ? "Device token" : "Phone (+E.164)"}<input value={f.recipient} placeholder={f.channel === "email" ? "you@example.com" : f.channel === "push" ? "fcm token" : "+15125550101"} onChange={(e) => setF({ ...f, recipient: e.target.value })} /></label>
        {f.channel === "email" && <label className="field">Subject<input value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} /></label>}
      </div>
      <textarea className="mt" style={{ minHeight: 60, fontFamily: "inherit" }} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} />
      <div className="row mt">
        <button disabled={!f.recipient || !f.body} onClick={send}>Send</button>
        {result && <Badge kind={result.status === "sent" ? (result.delivery_mode === "console" ? "warn" : "ok") : "bad"}>{result.status}{result.delivery_mode ? ` · ${result.delivery_mode}` : ""}{result.error_message ? ` — ${result.error_message}` : ""}</Badge>}
      </div>
    </div>
  );
}

const SENDERS: [string, string][] = [["", "All senders"], ["campaign", "Campaign"], ["automation", "Automation flow"], ["retry", "Automatic retry"],
  ["report", "Scheduled report"], ["seo_agent", "SEO agent"], ["super_admin", "Super admin"], ["system", "Platform"]];

/** Every message notification-svc handled, filterable — what was sent, by whom, and what happened. */
function DeliveryLog({ hours, version }: { hours: number; version: number }) {
  const [f, setF] = useState({ channel: "", status: "", sender: "" });
  const log = useLoad(() => must<any[]>(get(`/api/v1/notifications/notifications?hours=${hours}&limit=100${f.channel ? `&channel=${f.channel}` : ""}${f.status ? `&status=${f.status}` : ""}${f.sender ? `&sender=${f.sender}` : ""}`)), [hours, f.channel, f.status, f.sender, version]);
  const outcome = (n: any) => {
    if (n.status === "failed" || ["undelivered", "failed"].includes(n.provider_status)) return <Badge kind="bad">failed</Badge>;
    if (n.status === "skipped_opted_out") return <Badge>opted out</Badge>;
    if (n.delivery_mode === "console") return <Badge kind="warn">console only</Badge>;
    return <Badge kind="ok">{n.answered_by ? n.answered_by.replace(/_/g, " ") : n.provider_status || "sent"}</Badge>;
  };
  return (
    <div className="card mb table-wrap">
      <div className="row between">
        <h3>Delivery log</h3>
        <div className="row">
          <select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}><option value="">All channels</option>{["email", "sms", "push", "voice"].map((c) => <option key={c}>{c}</option>)}</select>
          <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">Any outcome</option><option value="sent">sent</option><option value="failed">failed</option><option value="skipped_opted_out">opted out</option></select>
          <select value={f.sender} onChange={(e) => setF({ ...f, sender: e.target.value })}>{SENDERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        </div>
      </div>
      <ErrorNote error={log.error} />
      {log.loading && !log.data ? <Loading /> : !(log.data || []).length ? <Empty>No messages match.</Empty> : (
        <table className="mt"><thead><tr><th>When</th><th>Message</th><th>To</th><th>Channel</th><th>Sent by</th><th>Outcome</th></tr></thead>
          <tbody>{(log.data || []).map((n) => (
            <tr key={n.id}>
              <td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(n.created_at)}</td>
              <td style={{ maxWidth: 340 }}><b>{n.subject || n.preview.slice(0, 60) || "—"}</b>{n.subject && <div className="muted" style={{ fontSize: 12 }}>{n.preview.slice(0, 90)}</div>}{n.error_message && <div style={{ color: "var(--bad)", fontSize: 12 }}>{n.error_message}</div>}</td>
              <td className="mono" style={{ fontSize: 12 }}>{n.recipient}</td>
              <td>{n.channel}</td>
              <td>{n.sender_label}{n.automated && <Badge kind="info">auto</Badge>}</td>
              <td>{outcome(n)}</td>
            </tr>
          ))}</tbody></table>
      )}
    </div>
  );
}

export default function Messaging() {
  const run = useAction();
  const [hours, setHours] = useState(24);
  const integrations = useLoad(() => must<any[]>(get("/api/v1/rbac/integrations")));
  const summary = useLoad(() => must<any>(get(`/api/v1/notifications/summary?hours=${hours}`)), [hours]);
  const recent = useLoad(() => must<any[]>(get("/api/v1/notifications/notifications")));
  const autopilot = useLoad(() => must<any>(get("/api/v1/marketing/autopilot")));
  const journeys = useLoad(() => must<any[]>(get("/api/v1/marketing/journeys")));
  const policy = useLoad(() => must<any>(get("/api/v1/marketing/settings")));
  const agent = useLoad(async () => (await must<any[]>(get("/api/v1/ai/agents/registry"))).find((a) => a.key === "marketing_content_agent"));
  const channels = Object.entries(summary.data?.channels || {}) as [string, any][];
  const failures = (recent.data || []).filter((n) => n.status === "failed" || ["undelivered", "failed"].includes(n.provider_status)).slice(0, 8);
  const [version, setVersion] = useState(0);
  const t = summary.data?.totals;
  const rate = summary.data?.delivery_rate;
  const period = hours === 24 ? "last 24 hours" : hours === 168 ? "last 7 days" : "last 30 days";
  const setAutopilot = (body: any, label: string) => run(label, () => must(api("PUT", "/api/v1/marketing/autopilot", body))).then(autopilot.reload);
  return (
    <>
      <PageHead title="Messaging & automation" text="Which outward channels are really live, what was actually delivered, and the marketing automation running on this tenant — with the controls to stop it.">
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>{[24, 168, 720].map((h) => <option key={h} value={h}>last {h === 24 ? "24 hours" : h === 168 ? "7 days" : "30 days"}</option>)}</select>
        <button className="ghost" onClick={() => { integrations.reload(); summary.reload(); recent.reload(); autopilot.reload(); journeys.reload(); setVersion(version + 1); }}>Refresh</button>
      </PageHead>

      <div className="grid cols-4 mb">
        <div className="card kpi"><div className="l">Sent</div><div className="n">{t ? t.total.toLocaleString() : "—"}</div><div className="d">{period} · all channels</div></div>
        <div className="card kpi"><div className="l">Delivery rate</div><div className="n" style={{ color: rate == null ? undefined : rate >= 0.97 ? "var(--ok)" : "var(--bad)" }}>{rate == null ? "—" : `${(rate * 100).toFixed(1)}%`}</div><div className="d">{rate == null ? "nothing went through a provider yet" : rate >= 0.97 ? "SLA 97% ✓" : "below the 97% SLA"}</div></div>
        <div className="card kpi"><div className="l">Automated</div><div className="n">{t ? t.automated.toLocaleString() : "—"}</div><div className="d">{t && t.total ? `${Math.round((t.automated / t.total) * 100)}% — flows, agents, retries, reports` : "flows, agents, retries, reports"}</div></div>
        <div className="card kpi"><div className="l">Failed</div><div className="n" style={{ color: t?.failed ? "var(--bad)" : undefined }}>{t ? t.failed : "—"}</div><div className="d">{t?.console ? `${t.console} console only (no provider)` : "including provider-reported"}</div></div>
      </div>

      <div className="card mb">
        <div className="row between"><h3>Channel readiness</h3><Link href="/admin/integrations">Manage integrations</Link></div>
        <ErrorNote error={integrations.error} />
        {integrations.loading ? <Loading /> : (
          <table className="mt"><tbody>{CHANNELS.map(([label, provider, use]) => {
            const [kind, text] = readiness(integrations.data || [], provider);
            return <tr key={label}><td><b>{label}</b><div className="muted" style={{ fontSize: 12 }}>{use}</div></td><td className="mono muted">{provider}</td><td><Badge kind={kind}>{text}</Badge></td></tr>;
          })}</tbody></table>
        )}
      </div>

      <div className="card mb">
        <div className="row between">
          <h3>Delivery by channel</h3>
          <span className="muted">{period}</span>
        </div>
        <div className="sub">“Console only” messages were rendered and logged but never left VOYQI, because no provider was set up for that channel.</div>
        {summary.loading ? <Loading /> : !channels.length ? <Empty>Nothing sent in this period.</Empty> : (
          <table className="mt"><thead><tr><th>Channel</th><th>Delivered</th><th>Console only</th><th>Failed</th><th>Opted out</th></tr></thead>
            <tbody>{channels.map(([ch, c]) => (
              <tr key={ch}><td><b>{ch}</b></td><td>{c.delivered}</td><td className="muted">{c.console}</td><td style={{ color: c.failed ? "var(--bad)" : undefined }}>{c.failed}</td><td className="muted">{c.opted_out}</td></tr>
            ))}</tbody></table>
        )}
        {(summary.data?.by_sender || []).length > 0 && (
          <div className="row mt">{summary.data.by_sender.map((x: any) => <span key={x.sender} className="badge">{x.label}: <b>{x.count}</b></span>)}</div>
        )}
        {failures.length > 0 && (
          <>
            <h4 className="mt">Recent failures</h4>
            <table><tbody>{failures.map((n) => (
              <tr key={n.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(n.created_at)}</td><td>{n.channel}</td><td className="mono">{n.recipient}</td><td style={{ color: "var(--bad)" }}>{n.error_message}</td></tr>
            ))}</tbody></table>
          </>
        )}
      </div>

      <DeliveryLog hours={hours} version={version} />

      <SendTest onSent={() => { summary.reload(); recent.reload(); setVersion((v) => v + 1); }} />

      <div className="grid cols-2">
        <div className="card">
          <div className="row between"><h3>Marketing Content Agent</h3>{agent.data && <StatusBadge value={agent.data.status} />}</div>
          <div className="sub">Writes AI drafts, AI-studio variants and autopilot posts. Pausing it stops all three for this tenant.</div>
          <div className="row mt"><Link href="/admin/ai/agents"><button className="sm ghost">Agent controls</button></Link><Link href="/admin/ai/prompts"><button className="sm ghost">Its prompts</button></Link></div>
          <h4 className="mt">Social autopilot</h4>
          {autopilot.loading ? <Loading /> : autopilot.error ? <ErrorNote error={autopilot.error} /> : (
            <>
              <div className="row">
                <Badge kind={autopilot.data.enabled ? "ok" : ""}>{autopilot.data.enabled ? "on" : "off"}</Badge>
                <Badge kind={autopilot.data.mode === "autopublish" ? "warn" : "info"}>{autopilot.data.mode === "autopublish" ? "auto-publishes" : "suggests — a person approves"}</Badge>
                <span className="muted">{autopilot.data.posts_per_week}/week · last planned {fmtTime(autopilot.data.last_planned_at)}</span>
              </div>
              <div className="row mt">
                {autopilot.data.mode === "autopublish" && <button className="sm" onClick={() => setAutopilot({ mode: "suggest" }, "Autopilot now waits for approval")}>Require approval</button>}
                {autopilot.data.enabled && <button className="sm danger" onClick={() => confirm("Turn the social autopilot off for this tenant?") && setAutopilot({ enabled: false }, "Autopilot off")}>Turn off</button>}
              </div>
            </>
          )}
        </div>
        <div className="card">
          <h3>Automation flows</h3>
          <div className="sub">Journeys running on platform events. Pausing one holds every contact at their current step.</div>
          {journeys.loading ? <Loading /> : !(journeys.data || []).length ? <Empty>No flows.</Empty> : (
            <table className="mt"><tbody>{(journeys.data || []).map((j) => (
              <tr key={j.id}><td><b>{j.name}</b><div className="muted mono" style={{ fontSize: 11 }}>{j.trigger_event}</div></td><td><StatusBadge value={j.status} /></td>
                <td className="muted">{j.enrollments.active || 0} in flow</td>
                <td>{j.status === "active" && <button className="sm ghost" onClick={() => run("Paused", () => must(api("PATCH", `/api/v1/marketing/journeys/${j.id}`, { status: "paused" }))).then(journeys.reload)}>Pause</button>}</td></tr>
            ))}</tbody></table>
          )}
          {policy.data && <div className="muted mt" style={{ fontSize: 12 }}>Voice calling window: {policy.data.voice_window_start}:00–{policy.data.voice_window_end}:00 {policy.data.timezone} ({policy.data.voice_window_open_now ? "open now" : "closed now"})</div>}
        </div>
      </div>
    </>
  );
}
