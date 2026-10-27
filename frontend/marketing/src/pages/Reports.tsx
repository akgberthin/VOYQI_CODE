import { useState } from "react";
import { Link } from "react-router-dom";
import { getTenant } from "@shared/api";
import { Empty, ErrorNote, Loading, PageHead } from "@shared/ui";
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
  if (!data && !loading && !error) load(days);
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
      </PageHead>
      <ErrorNote error={error} />
      {loading || !data ? <Loading /> : (
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
                  <tr key={c.campaign_id}><td><Link to={`/campaigns/${c.campaign_id}`}>{c.name}</Link></td><td><Ch c={c.channel} /></td><td>{c.delivered.toLocaleString()}</td><td>{c.conversions}</td><td>{money(c.revenue_usd)}</td></tr>
                ))}</tbody></table>
            )}
          </div>
        </>
      )}
    </>
  );
}
