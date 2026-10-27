import { useState } from "react";
import { Link } from "react-router-dom";
import { api, get, must } from "../api";
import { Badge, Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "../components/ui";

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
  const failures = (recent.data || []).filter((n) => n.status === "failed").slice(0, 8);
  const setAutopilot = (body: any, label: string) => run(label, () => must(api("PUT", "/api/v1/marketing/autopilot", body))).then(autopilot.reload);
  return (
    <>
      <PageHead title="Messaging & automation" text="Which outward channels are really live, what was actually delivered, and the marketing automation running on this tenant — with the controls to stop it.">
        <button className="ghost" onClick={() => { integrations.reload(); summary.reload(); recent.reload(); autopilot.reload(); journeys.reload(); }}>Refresh</button>
      </PageHead>

      <div className="card mb">
        <div className="row between"><h3>Channel readiness</h3><Link to="/integrations">Manage integrations</Link></div>
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
          <h3>Delivery</h3>
          <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>{[24, 168, 720].map((h) => <option key={h} value={h}>last {h === 24 ? "24 hours" : h === 168 ? "7 days" : "30 days"}</option>)}</select>
        </div>
        <div className="sub">“Console only” messages were rendered and logged but never left VOYQI, because no provider was set up for that channel.</div>
        {summary.loading ? <Loading /> : !channels.length ? <Empty>Nothing sent in this period.</Empty> : (
          <table className="mt"><thead><tr><th>Channel</th><th>Delivered</th><th>Console only</th><th>Failed</th><th>Opted out</th></tr></thead>
            <tbody>{channels.map(([ch, c]) => (
              <tr key={ch}><td><b>{ch}</b></td><td>{c.delivered}</td><td className="muted">{c.console}</td><td style={{ color: c.failed ? "var(--bad)" : undefined }}>{c.failed}</td><td className="muted">{c.opted_out}</td></tr>
            ))}</tbody></table>
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

      <div className="grid cols-2">
        <div className="card">
          <div className="row between"><h3>Marketing Content Agent</h3>{agent.data && <StatusBadge value={agent.data.status} />}</div>
          <div className="sub">Writes AI drafts, AI-studio variants and autopilot posts. Pausing it stops all three for this tenant.</div>
          <div className="row mt"><Link to="/ai/agents"><button className="sm ghost">Agent controls</button></Link><Link to="/ai/prompts"><button className="sm ghost">Its prompts</button></Link></div>
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
