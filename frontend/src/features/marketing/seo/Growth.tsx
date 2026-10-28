"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import { useSession } from "@/lib/session";
import { Badge, Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, pretty, useAction, useLoad } from "@/lib/ui";
import { Bars, Explain, Kpi, LineChart, Missing, ModeBadge, SeoPage, fmtN, fmtPct, seo, signed, type Site } from "./seo";

const ENGINE_LABEL: Record<string, string> = { chatgpt: "ChatGPT", perplexity: "Perplexity", claude: "Claude", google_ai: "Google AI" };

// ── Growth overview ───────────────────────────────────────────────────
export function Overview() {
  return (
    <SeoPage title="SEO & GEO growth" text="Organic search, rankings, content, authority and AI-answer visibility for each site, with what the agents will do next. Numbers come from the site's connected sources; anything a source isn't connected for says so.">
      {(site) => <OverviewBody site={site} />}
    </SeoPage>
  );
}

function OverviewBody({ site }: { site: Site }) {
  const d = useLoad(async () => {
    const [dash, strategy, plan, activity] = await Promise.all([
      seo.get(`/sites/${site.id}/dashboard`), seo.get(`/sites/${site.id}/strategy`), seo.get(`/sites/${site.id}/autopilot/plan`), seo.get<any[]>(`/sites/${site.id}/activity?limit=15`),
    ]);
    return { dash, strategy, plan, activity };
  }, [site.id]);
  if (d.loading && !d.data) return <Loading what="Reading the site's numbers" />;
  if (!d.data) return <ErrorNote error={d.error} />;
  const { dash, strategy, plan, activity } = d.data;
  const o = dash.organic, r = dash.rankings, c = dash.content, a = dash.authority, ai = dash.ai_visibility, t = dash.technical;
  const top = (strategy?.strategy?.initiatives || []).slice(0, 3);
  return (
    <>
      <div className="grid cols-4 mb">
        <Kpi label={`Organic clicks · ${dash.window_days}d`} value={o.has_data ? fmtN(o.clicks) : "—"} sub={o.has_data ? `${signed(o.clicks_growth_pct)} vs previous · CTR ${fmtPct(o.ctr)}` : <Missing text={o.missing} />} />
        <Kpi label="Keywords in top 10" value={r.has_data ? `${r.top_10}/${r.tracked}` : "—"} sub={r.has_data ? `avg position ${o.avg_position ?? "—"}` : <Missing text={r.missing} />} />
        <Kpi label="Content published" value={fmtN(c.published_period)} sub={`${fmtN(c.published_total)} total · ${fmtN(c.programmatic_total)} programmatic`} />
        <Kpi label="Authority" value={a.has_data ? a.latest?.authority_score ?? "—" : "—"} sub={a.has_data ? `${fmtN(a.latest?.referring_domains)} referring domains` : <Missing text={a.missing} />} />
      </div>
      <div className="split mb">
        <div className="card">
          <div className="row between"><h3>Organic clicks</h3><span className="muted">{fmtN(o.impressions)} impressions</span></div>
          {o.has_data ? <LineChart label="Organic clicks per day" points={(o.series || []).map((p: any) => ({ x: p.day, y: p.clicks }))} /> : <Missing text={o.missing} />}
        </div>
        <div className="card">
          <h3>Where keywords rank</h3>
          {r.has_data ? <div className="mt"><Bars rows={Object.entries(r.distribution || {}).map(([k, v]) => [`Positions ${k}`, v as number])} /></div> : <Missing text={r.missing} />}
        </div>
      </div>
      <div className="split mb">
        <div className="card">
          <div className="row between"><h3>AI-answer visibility</h3><Link href="/marketing/seo/geo">GEO</Link></div>
          {ai.has_data ? (
            <>
              <div className="row mt"><Badge kind="gold">score {ai.overall.score}</Badge><span className="muted">mentioned in {fmtPct(ai.overall.mention_rate, 0)} · cited in {fmtPct(ai.overall.citation_rate, 0)} · share of voice {fmtPct(ai.share_of_voice, 0)}</span></div>
              <table className="mt"><thead><tr><th>Engine</th><th>Checks</th><th>Mentioned</th><th>Cited</th><th>Score</th></tr></thead>
                <tbody>{Object.entries(ai.by_engine || {}).map(([e, v]: [string, any]) => <tr key={e}><td>{ENGINE_LABEL[e] || pretty(e)}</td><td>{v.checks}</td><td>{fmtPct(v.mention_rate, 0)}</td><td>{fmtPct(v.citation_rate, 0)}</td><td><b>{v.score}</b></td></tr>)}</tbody></table>
              <div className="muted mt" style={{ fontSize: 12 }}>{ai.disclaimer}</div>
            </>
          ) : <Missing text={ai.missing || "record or run AI-answer checks"} />}
        </div>
        <div className="card">
          <h3>Site health</h3>
          <div className="kv mt">
            <span>Technical</span><span>{t.has_data ? <>health {t.health_score}/100 · {Object.values(t.open_issues || {}).reduce((x: number, y: any) => x + y, 0)} open issues</> : <Missing text={t.missing} />}</span>
            <span>Local</span><span>{dash.local.has_data ? `${dash.local.locations} location(s) · profile ${dash.local.avg_profile_score}/100 · in the map pack ${fmtPct(dash.local.pack_rate, 0)}` : <Missing text={dash.local.missing} />}</span>
            <span>Traffic</span><span>{dash.traffic.has_data ? `${fmtN(dash.traffic.organic_sessions)} organic sessions · ${fmtN(dash.traffic.conversions)} conversions · ${signed(dash.traffic.organic_growth_pct)}` : <Missing text={dash.traffic.missing} />}</span>
            <span>Tasks</span><span>{Object.entries(dash.tasks.by_status || {}).map(([k, v]) => `${v} ${pretty(k)}`).join(" · ") || "none"} · {dash.tasks.done_period} done this period</span>
          </div>
        </div>
      </div>
      <div className="split">
        <div className="card">
          <div className="row between"><h3>Top priorities</h3><Link href="/marketing/seo/strategy">Strategy</Link></div>
          {!top.length ? <div className="empty">No strategy yet — generate one under Strategy.</div> : top.map((i: any) => (
            <div key={i.key} className="mt"><div className="row between"><b>{i.title}</b><Badge kind="gold">priority {i.priority}</Badge></div><div className="sub">{i.rationale}</div></div>
          ))}
          <h4 className="mt">Autopilot</h4>
          <div className="muted">{plan.eligible.length} task(s) can run now · {plan.held.length} held · {plan.slots_free} free slot(s)</div>
        </div>
        <div className="card">
          <div className="row between"><h3>Live activity</h3><Link href="/marketing/seo/reports">All</Link></div>
          <ActivityList rows={activity} />
        </div>
      </div>
    </>
  );
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function ActivityList({ rows }: { rows: any[] }) {
  const { me } = useSession();
  if (!rows.length) return <div className="empty">Nothing yet.</div>;
  // People are recorded by their user id; show who that is when we can.
  const who = (a: string) => (a.startsWith("ai:") ? <Badge kind="ai">{pretty(a.slice(3))}</Badge> : a === me?.user_id ? "you" : UUID.test(a) ? "a person" : a);
  return (
    <table className="mt"><tbody>{rows.map((e) => (
      <tr key={e.id}><td className="muted" style={{ whiteSpace: "nowrap", fontSize: 12 }}>{fmtTime(e.created_at)}</td><td><Badge>{e.kind}</Badge></td><td>{e.message}</td>
        <td className="muted" style={{ fontSize: 12 }}>{who(String(e.actor))}</td></tr>
    ))}</tbody></table>
  );
}

// ── Strategy, tasks and autopilot ─────────────────────────────────────
const CAP_LABEL: Record<string, string> = {
  max_publish_per_week: "Publishes per week", max_pseo_pages_per_day: "Programmatic pages per day", max_outreach_per_day: "Outreach emails per day",
  max_auto_fixes_per_day: "Automatic fixes per day", max_concurrent_tasks: "Tasks at once",
};

export function Strategy() {
  return (
    <SeoPage title="Strategy & autopilot" text="The strategy is built from the site's real data and ordered by expected impact per effort; each initiative becomes tasks. What autopilot may run on its own is set here — mode, caps and per-task overrides — and the hard gates hold whatever you choose.">
      {(site, reload) => <StrategyBody site={site} onSiteChange={reload} />}
    </SeoPage>
  );
}

function StrategyBody({ site, onSiteChange }: { site: Site; onSiteChange: () => void }) {
  const run = useAction();
  const [status, setStatus] = useState("");
  const d = useLoad(async () => {
    const [strategy, history, plan, types] = await Promise.all([
      seo.get(`/sites/${site.id}/strategy`), seo.get<any[]>(`/sites/${site.id}/strategy/history`), seo.get(`/sites/${site.id}/autopilot/plan`), seo.get<any[]>("/task-types"),
    ]);
    return { strategy, history, plan, types };
  }, [site.id]);
  const tasks = useLoad(() => seo.get<any[]>(`/sites/${site.id}/tasks${status ? `?status=${status}` : ""}`), [site.id, status]);
  const [nt, setNt] = useState({ type: "custom", title: "", description: "" });
  const reloadAll = () => { d.reload(); tasks.reload(); };
  if (d.loading && !d.data) return <Loading />;
  if (!d.data) return <ErrorNote error={d.error} />;
  const { strategy, history, plan, types } = d.data;
  const typeLabel = (t: string) => types.find((x) => x.type === t)?.label || pretty(t);
  const initiatives = strategy?.strategy?.initiatives || [];
  return (
    <>
      <div className="row mb">
        <button onClick={() => run("New strategy generated — its tasks are queued", () => seo.post(`/sites/${site.id}/strategy/generate`)).then(reloadAll)}>Generate strategy</button>
        <button className="ghost" onClick={() => run("Replanned", async () => { const r = await seo.post(`/sites/${site.id}/replan`); return r; }).then(reloadAll)}>Replan tasks</button>
      </div>
      <Explain title="How to read this.">Priority = impact × confidence ÷ effort. Evidence is the data each initiative rests on; when a source isn&apos;t connected the initiative says so instead of guessing.</Explain>
      {!strategy?.strategy ? <Empty>No strategy yet — press Generate strategy.</Empty> : (
        <div className="card mb">
          <div className="row between"><h3>Strategy v{strategy.strategy.version}</h3><span className="muted">{fmtTime(strategy.strategy.created_at)} · {strategy.strategy.generated_by}</span></div>
          <div className="sub">{strategy.strategy.summary}</div>
          {strategy.strategy.narrative && <div className="pre mt">{strategy.strategy.narrative}</div>}
          <table className="mt"><thead><tr><th>Initiative</th><th>Area</th><th>Impact</th><th>Effort</th><th>Confidence</th><th>Priority</th><th>Tasks</th></tr></thead>
            <tbody>{initiatives.map((i: any) => (
              <tr key={i.key}><td><b>{i.title}</b><div className="muted" style={{ fontSize: 12 }}>{i.rationale}</div></td><td><Badge>{i.area}</Badge></td><td>{i.impact}</td><td>{i.effort}</td><td>{fmtPct(i.confidence, 0)}</td><td><b>{i.priority}</b></td><td>{i.tasks.length}</td></tr>
            ))}</tbody></table>
        </div>
      )}
      <div className="split mb">
        <div className="card">
          <h3>What may run right now</h3>
          <div className="sub">{plan.in_progress} in progress · {plan.slots_free} free slot(s) of {plan.caps.max_concurrent_tasks}</div>
          {plan.eligible.length ? <table className="mt"><tbody>{plan.eligible.map((e: any) => <tr key={e.task_id}><td>{e.title}</td><td><Badge kind="ai">{pretty(e.owning_agent)}</Badge></td><td className="muted">{e.priority}</td></tr>)}</tbody></table> : <div className="empty">Nothing eligible.</div>}
          {plan.held.length > 0 && <>
            <h4 className="mt">Held ({plan.held.length})</h4>
            <table><tbody>{plan.held.slice(0, 12).map((h: any) => <tr key={h.task_id}><td>{h.title}</td><td className="muted" style={{ fontSize: 12 }}>{h.reason}</td></tr>)}</tbody></table>
          </>}
        </div>
        <AutonomySettings site={site} types={types} onSaved={() => { onSiteChange(); d.reload(); }} />
      </div>
      <div className="card mb">
        <div className="row between">
          <h3>Task queue</h3>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>{["", "ready", "awaiting_approval", "in_progress", "done", "failed", "blocked", "dismissed"].map((s) => <option key={s} value={s}>{s ? pretty(s) : "all"}</option>)}</select>
        </div>
        {tasks.loading ? <Loading /> : !(tasks.data || []).length ? <div className="empty">No tasks.</div> : (
          <div className="table-wrap"><table className="mt">
            <thead><tr><th>Task</th><th>Agent</th><th>Priority</th><th>Source</th><th>Status</th><th /></tr></thead>
            <tbody>{(tasks.data || []).map((t) => (
              <tr key={t.id}>
                <td><b>{t.title}</b><div className="muted" style={{ fontSize: 12 }}>{typeLabel(t.type)}{t.approval_reason ? ` · ${t.approval_reason}` : ""}{t.error ? ` · ${t.error}` : ""}</div></td>
                <td><Badge kind="ai">{pretty(t.owning_agent)}</Badge></td><td>{t.priority}</td><td className="muted">{t.source}</td><td><StatusBadge value={t.status} /></td>
                <td><div className="row">
                  {t.status === "awaiting_approval" && <button className="sm ok" onClick={() => run("Approved", () => seo.post(`/tasks/${t.id}/approve`)).then(reloadAll)}>Approve</button>}
                  {["ready", "awaiting_approval", "blocked"].includes(t.status) && <button className="sm ghost" onClick={() => run("Dismissed", () => seo.post(`/tasks/${t.id}/dismiss`)).then(reloadAll)}>Dismiss</button>}
                </div></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
        <h4 className="mt">Add a task</h4>
        <div className="form">
          <label className="field">Type<select value={nt.type} onChange={(e) => setNt({ ...nt, type: e.target.value })}>{types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}</select></label>
          <label className="field">Title<input value={nt.title} placeholder="What should be done" onChange={(e) => setNt({ ...nt, title: e.target.value })} /></label>
          <label className="field">Details<input value={nt.description} onChange={(e) => setNt({ ...nt, description: e.target.value })} /></label>
        </div>
        <button className="mt" disabled={!nt.title} onClick={() => run("Task added", async () => { const r = await seo.post(`/sites/${site.id}/tasks`, nt); if (!r.created) throw new Error("An identical open task already exists."); }).then(() => { setNt({ ...nt, title: "", description: "" }); reloadAll(); })}>Add task</button>
      </div>
      <Intake site={site} onSaved={onSiteChange} />
      {history.length > 1 && (
        <div className="card mt">
          <h3>Strategy history</h3>
          <table className="mt"><tbody>{history.map((h) => <tr key={h.id}><td>v{h.version}</td><td className="muted">{fmtTime(h.created_at)}</td><td>{h.summary}</td></tr>)}</tbody></table>
        </div>
      )}
    </>
  );
}

function AutonomySettings({ site, types, onSaved }: { site: Site; types: any[]; onSaved: () => void }) {
  const run = useAction();
  const [caps, setCaps] = useState(site.caps);
  const [over, setOver] = useState<Record<string, string>>(site.autonomy_overrides || {});
  useEffect(() => { setCaps(site.caps); setOver(site.autonomy_overrides || {}); }, [site]);
  return (
    <div className="card">
      <h3>Caps & overrides</h3>
      <div className="sub">Autopilot never exceeds these, whatever the plan says.</div>
      <div className="form mt">{Object.keys(CAP_LABEL).map((k) => (
        <label key={k} className="field">{CAP_LABEL[k]}<input type="number" min={0} value={caps[k] ?? 0} onChange={(e) => setCaps({ ...caps, [k]: Number(e.target.value) })} /></label>
      ))}</div>
      <h4 className="mt">Per task type</h4>
      <div className="sub">“approval” makes a type wait for a person even in autopilot mode.</div>
      <table className="mt"><tbody>{types.map((t) => (
        <tr key={t.type}><td>{t.label}{!t.assisted_safe && <span className="muted" style={{ fontSize: 11 }}> · changes the site</span>}</td>
          <td><select value={over[t.type] || ""} onChange={(e) => { const v = e.target.value; const n = { ...over }; if (v) n[t.type] = v; else delete n[t.type]; setOver(n); }}>
            <option value="">default ({t.default_autonomy})</option><option value="auto">auto</option><option value="approval">approval</option>
          </select></td></tr>
      ))}</tbody></table>
      <button className="mt" onClick={() => run("Autonomy saved", () => seo.patch(`/sites/${site.id}`, { caps, autonomy_overrides: over })).then(onSaved)}>Save</button>
      <div className="notice warn mt"><b>What autopilot can never do.</b> Publish guardrail-held content or held programmatic pages, apply fixes outside the low-risk list, start sponsored links, change a location&apos;s name, address, phone or categories, or pursue link schemes.</div>
    </div>
  );
}

function Intake({ site, onSaved }: { site: Site; onSaved: () => void }) {
  const run = useAction();
  const reqs = useLoad(() => seo.get<any[]>(`/sites/${site.id}/requests`), [site.id]);
  const [senders, setSenders] = useState(site.authorized_requesters.join(", "));
  const [t, setT] = useState({ sender: "", subject: "", body: "" });
  useEffect(() => setSenders(site.authorized_requesters.join(", ")), [site]);
  return (
    <div className="card mb">
      <h3>Task requests by email</h3>
      <div className="sub">Emails from allowed senders become tasks. A request that reads like instructions to an AI waits for approval; anyone else is refused.</div>
      <div className="row mt">
        <input style={{ flex: 1 }} placeholder="Allowed senders: ops@voyqi.com, marketing@voyqi.com" value={senders} onChange={(e) => setSenders(e.target.value)} />
        <button className="ghost" onClick={() => run("Allowed senders saved", () => seo.patch(`/sites/${site.id}`, { authorized_requesters: senders.split(/[\s,;]+/).filter(Boolean) })).then(onSaved)}>Save senders</button>
      </div>
      <h4 className="mt">Send a test request</h4>
      <div className="form">
        <label className="field">From<input value={t.sender} placeholder="ops@voyqi.com" onChange={(e) => setT({ ...t, sender: e.target.value })} /></label>
        <label className="field">Subject<input value={t.subject} onChange={(e) => setT({ ...t, subject: e.target.value })} /></label>
      </div>
      <textarea className="mt" style={{ minHeight: 60, fontFamily: "inherit" }} placeholder="Please run a technical audit and refresh the pricing page." value={t.body} onChange={(e) => setT({ ...t, body: e.target.value })} />
      <button className="mt" disabled={!t.sender || !t.body} onClick={() => run("Request received", () => seo.post(`/sites/${site.id}/requests/email`, t)).then(() => { setT({ sender: t.sender, subject: "", body: "" }); reqs.reload(); })}>Send request</button>
      {(reqs.data || []).length > 0 && (
        <table className="mt"><thead><tr><th>Received</th><th>From</th><th>Request</th><th>Outcome</th><th /></tr></thead>
          <tbody>{(reqs.data || []).map((r) => (
            <tr key={r.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(r.received_at)}</td><td>{r.sender}</td><td><b>{r.subject}</b><div className="muted clip">{r.body}</div></td>
              <td><StatusBadge value={r.status} />{r.injection_suspected && <Badge kind="bad">instruction-like</Badge>}<div className="muted" style={{ fontSize: 12 }}>{r.parsed?.reason || (r.task_ids?.length ? `${r.task_ids.length} task(s)` : "")}</div></td>
              <td>{["received", "needs_clarification"].includes(r.status) && <button className="sm ghost" onClick={() => run("Converted to tasks", () => seo.post(`/requests/${r.id}/convert`)).then(reqs.reload)}>Convert</button>}</td></tr>
          ))}</tbody></table>
      )}
    </div>
  );
}

// ── Reports & activity ────────────────────────────────────────────────
export function Reports() {
  return <SeoPage title="Reports & activity" text="Progress reports (daily, weekly or monthly) emailed to the site's recipients, and everything the agents and people did.">{(site, reload) => <ReportsBody site={site} onSiteChange={reload} />}</SeoPage>;
}

function ReportsBody({ site, onSiteChange }: { site: Site; onSiteChange: () => void }) {
  const run = useAction();
  const reports = useLoad(() => seo.get<any[]>(`/sites/${site.id}/reports`), [site.id]);
  const [kind, setKind] = useState("");
  const activity = useLoad(() => seo.get<any[]>(`/sites/${site.id}/activity?limit=100${kind ? `&kind=${kind}` : ""}`), [site.id, kind]);
  const [open, setOpen] = useState<any>(null);
  const [period, setPeriod] = useState("weekly");
  const [rec, setRec] = useState({ frequency: site.report_frequency, recipients: site.report_recipients.join(", ") });
  const view = async (id: string) => setOpen(await seo.get(`/reports/${id}`));
  return (
    <>
      <div className="split mb">
        <div className="card">
          <h3>Generate a report</h3>
          <div className="row mt">
            <select value={period} onChange={(e) => setPeriod(e.target.value)}>{["daily", "weekly", "monthly"].map((p) => <option key={p}>{p}</option>)}</select>
            <button onClick={() => run("Report generated", async () => { const r = await seo.post(`/sites/${site.id}/reports/generate`, { period }); await view(r.id); }).then(reports.reload)}>Generate</button>
          </div>
        </div>
        <div className="card">
          <h3>Schedule</h3>
          <div className="form mt">
            <label className="field">Frequency<select value={rec.frequency} onChange={(e) => setRec({ ...rec, frequency: e.target.value })}>{["off", "daily", "weekly", "monthly"].map((p) => <option key={p}>{p}</option>)}</select></label>
            <label className="field">Recipients<input placeholder="cmo@voyqi.com, growth@voyqi.com" value={rec.recipients} onChange={(e) => setRec({ ...rec, recipients: e.target.value })} /></label>
          </div>
          <button className="mt" onClick={() => run("Report schedule saved", () => seo.patch(`/sites/${site.id}`, { report_frequency: rec.frequency, report_recipients: rec.recipients.split(/[\s,;]+/).filter(Boolean) })).then(onSiteChange)}>Save</button>
        </div>
      </div>
      {open && <ReportView report={open} onClose={() => setOpen(null)} onChanged={(r) => { setOpen(r); reports.reload(); }} />}
      <div className="card mb">
        <h3>Reports</h3>
        {reports.loading ? <Loading /> : !(reports.data || []).length ? <div className="empty">No reports yet.</div> : (
          <table className="mt"><tbody>{(reports.data || []).map((r) => (
            <tr key={r.id} className="click" onClick={() => view(r.id)}><td><Badge>{r.period}</Badge></td><td>{r.period_start} → {r.period_end}</td><td className="muted clip">{r.narrative.split("\n")[0]}</td><td className="muted">{fmtTime(r.created_at)}</td></tr>
          ))}</tbody></table>
        )}
      </div>
      <div className="card">
        <div className="row between"><h3>Activity</h3>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>{[["", "all"], ["strategy", "strategy"], ["task", "tasks"], ["content", "content"], ["pseo", "programmatic"], ["technical_fix", "technical"], ["ai_citation", "AI answers"], ["backlink", "links"], ["cro", "conversion"], ["local", "local"], ["keyword", "keywords"], ["research", "research"], ["report", "reports"], ["integration", "integrations"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        </div>
        {activity.loading ? <Loading /> : <ActivityList rows={activity.data || []} />}
      </div>
    </>
  );
}

function ReportView({ report, onClose, onChanged }: { report: any; onClose: () => void; onChanged: (r: any) => void }) {
  const run = useAction();
  const [text, setText] = useState(report.narrative);
  useEffect(() => setText(report.narrative), [report]);
  return (
    <div className="card mb" style={{ borderColor: "var(--gold)" }}>
      <div className="row between"><h3>{pretty(report.period)} report · {report.period_start} → {report.period_end}</h3><button className="sm ghost" onClick={onClose}>Close</button></div>
      <textarea className="mt" style={{ minHeight: 140, fontFamily: "inherit" }} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="row mt">
        <button className="ghost" disabled={text === report.narrative} onClick={() => run("Narrative saved", async () => onChanged({ ...report, ...(await seo.post(`/reports/${report.id}/narrative`, { narrative: text })) }))}>Save narrative</button>
        <button onClick={() => run("Report emailed", async () => { const r = await seo.post(`/reports/${report.id}/send`); if (!r.sent) throw new Error("Nobody to send it to — add recipients under Schedule."); })}>Email to recipients</button>
      </div>
      {report.summary && <details className="mt"><summary className="muted">Data behind this report</summary><pre className="json mt">{JSON.stringify(report.summary, null, 2)}</pre></details>}
    </div>
  );
}

// ── Integrations, sites and agents ────────────────────────────────────
export function Setup() {
  const run = useAction();
  const sites = useLoad(() => seo.get<Site[]>("/sites"));
  const [f, setF] = useState({ name: "", domain: "", competitors: "", description: "" });
  const add = () => run("Site added", () => seo.post("/sites", {
    name: f.name, domain: f.domain, competitors: f.competitors.split(/[\s,;]+/).filter(Boolean), business_profile: f.description ? { description: f.description } : {},
  })).then((ok) => { if (ok) { setF({ name: "", domain: "", competitors: "", description: "" }); sites.reload(); } });
  return (
    <>
      <PageHead title="Integrations & agents" text="The sites the Growth agents work on, the sources each one is connected to, and the fourteen specialist agents." />
      <Explain title="Nothing is simulated.">Every number comes from a connected source. Credentials live in super admin → Integrations (category SEO); here you choose a provider per source. The labelled “sample” provider serves demo data and only exists where the deployment allows it.</Explain>
      <div className="card mb">
        <h3>Add your site</h3>
        <div className="form mt">
          <label className="field">Name<input value={f.name} placeholder="VOYQI" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="field">Domain<input value={f.domain} placeholder="voyqi.com" onChange={(e) => setF({ ...f, domain: e.target.value })} /></label>
          <label className="field">Competitors<input value={f.competitors} placeholder="rival.com, other.com" onChange={(e) => setF({ ...f, competitors: e.target.value })} /></label>
          <label className="field">What you offer<input value={f.description} placeholder="Car subscription and mobility membership" onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        </div>
        <button className="mt" disabled={!f.name || !f.domain} onClick={add}>Add site</button>
      </div>
      <ErrorNote error={sites.error} />
      {(sites.data || []).length > 0 && (
        <SeoPage title="Connections" bare key={(sites.data || []).length}>{(site) => <Connections site={site} />}</SeoPage>
      )}
      <Agents />
    </>
  );
}

function Connections({ site }: { site: Site }) {
  const run = useAction();
  const list = useLoad(() => seo.get<any[]>(`/sites/${site.id}/integrations`), [site.id]);
  const [open, setOpen] = useState<string | null>(null);
  const [cfg, setCfg] = useState({ provider: "", credential_ref: "", config: "{}" });
  const [syncing, setSyncing] = useState(false);
  const edit = (i: any) => { setOpen(open === i.kind ? null : i.kind); setCfg({ provider: i.provider || i.providers[0] || "", credential_ref: i.credential_ref || "", config: JSON.stringify(i.config || {}, null, 2) }); };
  const save = (kind: string) => run("Saved — now test it", async () => {
    let config;
    try { config = JSON.parse(cfg.config || "{}"); } catch (e: any) { throw new Error(`Config isn't valid JSON: ${e.message}`); }
    await seo.put(`/sites/${site.id}/integrations/${kind}`, { provider: cfg.provider, credential_ref: cfg.credential_ref || null, config });
  }).then(list.reload);
  const sync = async () => { setSyncing(true); await run("Synced", async () => { const r = await seo.post(`/sites/${site.id}/integrations/sync`); return r; }); setSyncing(false); list.reload(); };
  return (
    <div className="card mb">
      <div className="row between"><h3>Connections — {site.domain}</h3><button className="ghost" disabled={syncing} onClick={sync}>{syncing ? "Syncing…" : "Sync all now"}</button></div>
      {list.loading ? <Loading /> : (
        <div className="table-wrap"><table className="mt">
          <thead><tr><th>Source</th><th>Powers</th><th>Provider</th><th>Status</th><th /></tr></thead>
          <tbody>{(list.data || []).map((i) => (
            <Fragment key={i.kind}>
              <tr>
                <td><b>{i.label}</b><div className="mono muted" style={{ fontSize: 11 }}>{i.kind}</div></td>
                <td className="muted" style={{ fontSize: 12, maxWidth: 320 }}>{i.powers.join(" · ")}</td>
                <td>{i.provider ? <span className="mono">{i.provider}</span> : <span className="muted">—</span>}{i.provider === "sample" && <Badge kind="warn">demo data</Badge>}</td>
                <td><ModeBadge status={i.status} />{i.last_error && <div style={{ color: "var(--bad)", fontSize: 12 }}>{i.last_error}</div>}{i.last_sync_at && <div className="muted" style={{ fontSize: 11 }}>synced {fmtTime(i.last_sync_at)}</div>}</td>
                <td><div className="row">
                  <button className="sm ghost" onClick={() => edit(i)}>{open === i.kind ? "Close" : i.provider ? "Edit" : "Connect"}</button>
                  {i.provider && <button className="sm ghost" onClick={() => run(`${i.label} tested`, async () => { const r = await seo.post(`/sites/${site.id}/integrations/${i.kind}/test`); if (r.status !== "connected") throw new Error(r.last_error || r.detail || `status: ${r.status}`); }).then(list.reload)}>Test</button>}
                  {i.provider && <button className="sm ghost" onClick={() => confirm(`Disconnect ${i.label}?`) && run("Disconnected", () => seo.del(`/sites/${site.id}/integrations/${i.kind}`)).then(list.reload)}>Disconnect</button>}
                </div></td>
              </tr>
              {open === i.kind && (
                <tr><td colSpan={5}>
                  <div className="form">
                    <label className="field">Provider<select value={cfg.provider} onChange={(e) => setCfg({ ...cfg, provider: e.target.value })}>{i.providers.map((p: string) => <option key={p}>{p}</option>)}</select></label>
                    <label className="field">Credential reference<input className="mono" placeholder="rbac://dataforseo (never the secret itself)" value={cfg.credential_ref} onChange={(e) => setCfg({ ...cfg, credential_ref: e.target.value })} /></label>
                  </div>
                  <label className="field mt">Config (JSON, no secrets)<textarea style={{ minHeight: 60 }} value={cfg.config} onChange={(e) => setCfg({ ...cfg, config: e.target.value })} /></label>
                  <button className="sm mt" onClick={() => save(i.kind)}>Save</button>
                </td></tr>
              )}
            </Fragment>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

function Agents() {
  const { data, error, loading } = useLoad(() => seo.get<any[]>("/agents"));
  return (
    <div className="card">
      <h3>Fourteen specialist agents, one orchestration service</h3>
      <div className="sub">They run in ai-orchestration-svc and act through the same API this console uses, meeting every hard gate a person would not. Pause one under super admin → AI agents.</div>
      <ErrorNote error={error} />
      {loading ? <Loading /> : (
        <div className="grid cols-2 mt">{(data || []).map((a) => (
          <div key={a.key} className="card" style={{ background: "var(--panel-2)" }}>
            <div className="row between"><b>{a.name}</b><StatusBadge value={a.status} /></div>
            <div className="sub mt">{a.description}</div>
            <div className="row mt">{a.task_types.map((t: string) => <Badge key={t}>{pretty(t)}</Badge>)}<span className="muted" style={{ fontSize: 12 }}>{a.decisions} decisions</span></div>
          </div>
        ))}</div>
      )}
    </div>
  );
}
