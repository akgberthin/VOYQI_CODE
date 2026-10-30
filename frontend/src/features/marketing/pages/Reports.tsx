"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getTenant } from "@/lib/api";
import { Empty, ErrorNote, Loading, PageHead, fmtTime, useAction, useLoad } from "@/lib/ui";
import { Ch } from "../bits";
import { mk, money } from "../mk";

export default function Reports() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const load = async (d: number) => {
    setLoading(true);
    try { setData(await mk.report(d)); setError(null); } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(days); }, []);
  const download = async () => {
    const res = await fetch(`/api/v1/marketing/reports.csv?days=${days}`, { credentials: "same-origin", headers: { "X-Tenant-Id": getTenant() } });
    const url = URL.createObjectURL(await res.blob());
    Object.assign(document.createElement("a"), { href: url, download: `marketing-report-${days}d.csv` }).click();
    URL.revokeObjectURL(url);
  };
  const max = Math.max(1, ...(data?.daily_delivered || []).map((d: any) => d.delivered));
  return (
    <>
      <PageHead title="Reports" text="What went out and what came back over a period: deliveries by channel (real deliveries only — console-only messages are listed separately), social posts, automation flows, leads and the campaigns that drove results.">
        <select value={days} onChange={(e) => { const d = Number(e.target.value); setDays(d); load(d); }}>{[7, 30, 90, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}</select>
        <button className="ghost" onClick={download}>Download CSV</button>
        <button className="ghost" onClick={() => downloadPdf(days)}>Download PDF</button>
      </PageHead>
      <ErrorNote error={error} />
      <ReportSchedule days={days} />
      <Schedules />
      {loading || !data ? (error ? null : <Loading />) : (
        <>
          <div className="grid cols-4 mb">
            {[["Delivered", Object.values(data.channels).reduce((a: number, c: any) => a + c.delivered, 0)],
              ["Leads captured", data.leads], ["Journey messages", data.journeys.messages_sent], ["Journeys completed", data.journeys.completed]].map(([l, v]) => (
              <div key={l as string} className="card kpi"><div className="l">{l}</div><div className="n">{Number(v).toLocaleString()}</div></div>
            ))}
          </div>
          <div className="card mb">
            <h3>Delivered per day</h3>
            {!data.daily_delivered.length ? <Empty>No sends in this period.</Empty> : (
              <div className="row mt" style={{ alignItems: "flex-end", gap: 6, height: 140 }}>{data.daily_delivered.map((d: any) => (
                <div key={d.date} title={`${d.date}: ${d.delivered}`} style={{ flex: 1, minWidth: 8, display: "grid", gap: 4, justifyItems: "center" }}>
                  <div style={{ width: "100%", height: Math.max(4, (d.delivered / max) * 110), background: "var(--gold)", borderRadius: 4 }} />
                  <span className="muted" style={{ fontSize: 10 }}>{d.date.slice(5)}</span>
                </div>
              ))}</div>
            )}
          </div>
          <div className="split mb">
            <div className="card table-wrap">
              <h3>By channel</h3>
              {!Object.keys(data.channels).length ? <Empty>No sends.</Empty> : (
                <table className="mt"><thead><tr><th>Channel</th><th>Sends</th><th>Targeted</th><th>Delivered</th><th>Console only</th><th>Failed</th><th>Suppressed</th></tr></thead>
                  <tbody>{Object.entries(data.channels).map(([ch, c]: [string, any]) => (
                    <tr key={ch}><td><Ch c={ch} /></td><td>{c.sends}</td><td>{c.targeted}</td><td><b>{c.delivered}</b></td><td className="muted">{c.console}</td><td style={{ color: c.failed ? "var(--bad)" : undefined }}>{c.failed}</td><td className="muted">{c.suppressed}</td></tr>
                  ))}</tbody></table>
              )}
            </div>
            <div className="card table-wrap">
              <h3>Social posts</h3>
              {!Object.keys(data.social).length ? <Empty>No posts.</Empty> : (
                <table className="mt"><thead><tr><th>Platform</th><th>Published</th><th>Console only</th><th>Scheduled</th><th>Failed</th></tr></thead>
                  <tbody>{Object.entries(data.social).map(([p, c]: [string, any]) => (
                    <tr key={p}><td><b>{p}</b></td><td>{c.published || 0}</td><td className="muted">{c.console || 0}</td><td>{c.scheduled || 0}</td><td style={{ color: c.failed ? "var(--bad)" : undefined }}>{c.failed || 0}</td></tr>
                  ))}</tbody></table>
              )}
            </div>
          </div>
          <div className="card table-wrap">
            <h3>Top campaigns</h3>
            {!data.top_campaigns.length ? <Empty>No results recorded in this period.</Empty> : (
              <table className="mt"><thead><tr><th>Campaign</th><th>Channel</th><th>Delivered</th><th>Conversions</th><th>Revenue</th></tr></thead>
                <tbody>{data.top_campaigns.map((c: any) => (
                  <tr key={c.campaign_id}><td><Link href={`/marketing/campaigns/${c.campaign_id}`}>{c.name}</Link></td><td><Ch c={c.channel} /></td><td>{c.delivered.toLocaleString()}</td><td>{c.conversions}</td><td>{money(c.revenue_usd)}</td></tr>
                ))}</tbody></table>
            )}
          </div>
        </>
      )}
    </>
  );
}

/** Who gets the report by email, how often, and a send-now. */
function ReportSchedule({ days }: { days: number }) {
  const run = useAction();
  const { data, reload } = useLoad(() => mk.settings());
  const [f, setF] = useState<{ frequency: string; recipients: string } | null>(null);
  useEffect(() => { if (data && !f) setF({ frequency: data.report_frequency || "off", recipients: (data.report_recipients || []).join(", ") }); }, [data, f]);
  if (!f) return null;
  const list = f.recipients.split(/[\s,;]+/).filter(Boolean);
  return (
    <div className="card mb">
      <div className="row between"><h3>Emailed reports</h3>{data?.report_last_sent_at && <span className="muted" style={{ fontSize: 12 }}>last sent {fmtTime(data.report_last_sent_at)}</span>}</div>
      <div className="form mt">
        <label className="field">Send automatically<select value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value })}><option value="off">Off</option><option value="weekly">Weekly (last 7 days)</option><option value="monthly">Monthly (last 30 days)</option></select></label>
        <label className="field" style={{ gridColumn: "span 2" }}>Recipients<input value={f.recipients} placeholder="cmo@voyqi.com, growth@voyqi.com" onChange={(e) => setF({ ...f, recipients: e.target.value })} /></label>
      </div>
      <div className="row mt">
        <button onClick={() => run("Report schedule saved", () => mk.saveSettings({ report_frequency: f.frequency, report_recipients: list })).then(reload)}>Save</button>
        <button className="ghost" disabled={!list.length} onClick={() => run("Report emailed", async () => {
          const r = await mk.sendReport(days, list);
          if (r.failed.length) throw new Error(`Sent ${r.sent}; failed: ${r.failed.join("; ")}`);
          if (r.console) throw new Error(`${r.console} of ${r.sent} only logged — no email provider is set up (super admin → Integrations).`);
        })}>Send this report now</button>
      </div>
    </div>
  );
}

async function downloadPdf(days: number) {
  const res = await fetch(`/api/v1/marketing/reports.pdf?days=${days}`, { credentials: "same-origin", headers: { "X-Tenant-Id": getTenant() } });
  if (!res.ok) return alert(`Could not build the PDF (HTTP ${res.status})`);
  const url = URL.createObjectURL(await res.blob());
  Object.assign(document.createElement("a"), { href: url, download: `marketing-report-${days}d.pdf` }).click();
  URL.revokeObjectURL(url);
}

const EMPTY_SCHEDULE = { name: "", frequency: "weekly", days: 7, channels: [] as string[], recipients: "", format: "pdf" as "csv" | "pdf" };

/** Named reports, each emailed on its own schedule with a CSV or PDF attached. */
function Schedules() {
  const run = useAction();
  const { data, reload } = useLoad(() => mk.schedules());
  const [f, setF] = useState(EMPTY_SCHEDULE);
  const save = () => run("Report scheduled", () => mk.createSchedule({ ...f, recipients: f.recipients.split(/[\s,;]+/).filter(Boolean) }))
    .then((ok) => { if (ok) { setF(EMPTY_SCHEDULE); reload(); } });
  const sendNow = (id: string) => run("Report emailed", async () => {
    const r = await mk.runSchedule(id);
    if (r.failed.length) throw new Error(`Sent ${r.sent}; failed: ${r.failed.join("; ")}`);
    if (r.console) throw new Error(`${r.console} of ${r.sent} only logged — no email provider is set up (super admin → Integrations).`);
  }).then(reload);
  return (
    <div className="card mb">
      <h3>Scheduled reports</h3>
      <div className="sub">Each report goes to its own recipients on its own schedule, with the numbers in the email and a CSV or PDF attached.</div>
      {(data || []).length > 0 && (
        <table className="mt"><thead><tr><th>Report</th><th>Frequency</th><th>Covers</th><th>Recipients</th><th>Last sent</th><th /></tr></thead>
          <tbody>{(data || []).map((s) => (
            <tr key={s.id} style={{ opacity: s.active ? 1 : 0.55 }}>
              <td><b>{s.name}</b> <span className="badge">{s.format.toUpperCase()}</span></td>
              <td>{s.frequency} · last {s.days} days</td>
              <td>{s.channels.length ? s.channels.join(", ") : "all channels"}</td>
              <td className="muted" style={{ fontSize: 12 }}>{s.recipients.join(", ")}</td>
              <td className="muted">{s.last_sent_at ? fmtTime(s.last_sent_at) : "never"}</td>
              <td><div className="row">
                <button className="sm" onClick={() => sendNow(s.id)}>Run now</button>
                <button className="sm ghost" onClick={() => run(s.active ? "Paused" : "Resumed", () => mk.updateSchedule(s.id, { active: !s.active })).then(reload)}>{s.active ? "Pause" : "Resume"}</button>
                <button className="sm danger" onClick={() => confirm(`Delete “${s.name}”?`) && run("Deleted", () => mk.deleteSchedule(s.id)).then(reload)}>×</button>
              </div></td>
            </tr>
          ))}</tbody></table>
      )}
      <div className="form mt">
        <label className="field">Report name<input value={f.name} placeholder="Weekly campaign performance" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field">Frequency<select value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value, days: e.target.value === "daily" ? 1 : e.target.value === "monthly" ? 30 : 7 })}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
        <label className="field">Period (days)<input type="number" min={1} max={365} value={f.days} onChange={(e) => setF({ ...f, days: Number(e.target.value) })} /></label>
        <label className="field">Format<select value={f.format} onChange={(e) => setF({ ...f, format: e.target.value as "csv" | "pdf" })}><option value="pdf">PDF</option><option value="csv">CSV</option></select></label>
        <label className="field" style={{ gridColumn: "span 2" }}>Recipients<input value={f.recipients} placeholder="cmo@voyqi.com, growth@voyqi.com" onChange={(e) => setF({ ...f, recipients: e.target.value })} /></label>
      </div>
      <div className="row mt">{["email", "sms", "push", "voice", "social"].map((c) => (
        <label key={c} className="badge" style={{ cursor: "pointer" }}><input type="checkbox" checked={f.channels.includes(c)} onChange={(e) => setF({ ...f, channels: e.target.checked ? [...f.channels, c] : f.channels.filter((x) => x !== c) })} /> {c}</label>
      ))}<span className="muted" style={{ fontSize: 12 }}>none ticked = all channels</span></div>
      <button className="mt" disabled={!f.name || !f.recipients.trim()} onClick={save}>Schedule report</button>
    </div>
  );
}
