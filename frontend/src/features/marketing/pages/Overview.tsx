"use client";

import Link from "next/link";
import { ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useLoad } from "@/lib/ui";
import { Ch } from "../bits";
import { CHANNELS, money, mk, pct } from "../mk";
import { seo } from "../seo/seo";

export default function Overview() {
  const { data, error, loading, reload } = useLoad(async () => {
    const [campaigns, snapshots, held, calendar, leads, sends] = await Promise.all([
      mk.campaigns(), mk.snapshots(), mk.drafts(undefined, "held_for_review"), mk.calendar(), mk.leads(), mk.sends(),
    ]);
    return { campaigns, snapshots, held, calendar, leads, sends };
  });
  if (loading && !data) return <Loading what="Loading marketing" />;
  if (!data) return <ErrorNote error={error} />;
  const sum = (k: "sent_count" | "open_count" | "click_count" | "conversion_count" | "revenue_usd") => data.snapshots.reduce((a, s) => a + (s[k] || 0), 0);
  const sent = sum("sent_count");
  const upcoming = data.calendar.filter((e) => new Date(e.scheduled_at) > new Date()).sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at)).slice(0, 8);
  const byChannel = CHANNELS.map((c) => ({ c, sent: data.snapshots.filter((s) => s.channel === c).reduce((a, s) => a + s.sent_count, 0) }));
  const maxCh = Math.max(1, ...byChannel.map((x) => x.sent));
  const active = data.campaigns.filter((c) => c.status === "active").length;
  return (
    <>
      <PageHead title="Marketing command center" text="Every campaign, channel and send — live from marketing-svc.">
        <Link href="/marketing/compose/email"><button>New message</button></Link>
        <button className="ghost" onClick={reload}>Refresh</button>
      </PageHead>
      <div className="grid cols-4 mb">
        <div className="card kpi"><div className="l">Delivered</div><div className="n">{sent.toLocaleString()}</div><div className="d">across {data.sends.filter((s) => s.status === "sent").length} sends</div></div>
        <div className="card kpi"><div className="l">Open / click rate</div><div className="n">{pct(sum("open_count"), sent)}</div><div className="d">click {pct(sum("click_count"), sent)}</div></div>
        <div className="card kpi"><div className="l">Conversions</div><div className="n">{sum("conversion_count").toLocaleString()}</div><div className="d">{money(sum("revenue_usd"))} revenue</div></div>
        <div className="card kpi"><div className="l">Campaigns</div><div className="n">{active} active</div><div className="d">{data.campaigns.length} total</div></div>
      </div>
      <SeoStrip />
      <LiveCampaigns campaigns={data.campaigns} snapshots={data.snapshots} sends={data.sends} />
      {data.held.length > 0 && <div className="notice warn mb">{data.held.length} draft{data.held.length > 1 ? "s" : ""} held by the brand/compliance guardrail — <Link href="/marketing/review">review them</Link>.</div>}
      <div className="split mb">
        <div className="card">
          <div className="row between"><h3>Coming up</h3><Link href="/marketing/calendar">Calendar</Link></div>
          {upcoming.length ? <table className="mt"><tbody>{upcoming.map((e) => (
            <tr key={e.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.scheduled_at)}</td><td><Ch c={e.channel} /></td><td>{e.title}</td></tr>
          ))}</tbody></table> : <div className="empty">Nothing scheduled.</div>}
        </div>
        <div className="card">
          <h3>Delivered by channel</h3>
          <div className="grid mt">{byChannel.map(({ c, sent: n }) => (
            <div key={c}><div className="row between"><Ch c={c} /><span className="muted">{n.toLocaleString()}</span></div><div className="bar mt" style={{ marginTop: 6 }}><div style={{ width: `${(n / maxCh) * 100}%` }} /></div></div>
          ))}</div>
        </div>
      </div>
      <div className="mb"><SendTimes /></div>
      <div className="split">
        <div className="card">
          <div className="row between"><h3>Campaigns</h3><Link href="/marketing/campaigns">All</Link></div>
          <table className="mt"><tbody>{data.campaigns.slice(0, 8).map((c) => (
            <tr key={c.id}><td><Link href={`/marketing/campaigns/${c.id}`}>{c.name}</Link></td><td><Ch c={c.channel} /></td><td><StatusBadge value={c.status} /></td></tr>
          ))}</tbody></table>
        </div>
        <div className="card">
          <div className="row between"><h3>Latest leads</h3><Link href="/marketing/leads">All</Link></div>
          {data.leads.length ? <table className="mt"><tbody>{data.leads.slice(-8).reverse().map((l) => (
            <tr key={l.id}><td>{l.name}</td><td className="muted">{l.email || l.phone}</td><td className="muted">{l.landing_page}</td></tr>
          ))}</tbody></table> : <div className="empty">No leads yet.</div>}
        </div>
      </div>
    </>
  );
}

/** The active campaigns with their latest numbers, like the reference dashboard's live cards. */
function LiveCampaigns({ campaigns, snapshots, sends }: { campaigns: any[]; snapshots: any[]; sends: any[] }) {
  const live = campaigns.filter((c) => c.status === "active").map((c) => {
    const s = snapshots.filter((x) => x.campaign_id === c.id);
    const sum = (k: string) => s.reduce((a, x) => a + (x[k] || 0), 0);
    const next = sends.filter((x) => x.campaign_id === c.id && x.status === "scheduled").sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at))[0];
    return { c, sent: sum("sent_count"), open: sum("open_count"), click: sum("click_count"), conv: sum("conversion_count"), next };
  }).sort((a, b) => b.sent - a.sent).slice(0, 3);
  if (!live.length) return null;
  return (
    <div className="grid cols-3 mb">{live.map(({ c, sent, open, click, conv, next }) => (
      <Link key={c.id} href={`/marketing/campaigns/${c.id}`} className="card" style={{ textDecoration: "none", color: "inherit" }}>
        <div className="row between"><b className="clip" style={{ maxWidth: 220 }}>{c.name}</b><StatusBadge value={next && !sent ? "scheduled" : "active"} /></div>
        <div className="row mt"><Ch c={c.channel} />{next && <span className="muted" style={{ fontSize: 12 }}>next {fmtTime(next.scheduled_at)}</span>}</div>
        <div className="grid cols-4 mt" style={{ gap: 6 }}>{[["Sent", sent.toLocaleString()], ["Open", pct(open, sent)], ["Click", pct(click, sent)], ["Convert", conv.toLocaleString()]].map(([l, v]) => (
          <div key={l}><div style={{ fontWeight: 800, fontSize: 17 }}>{v}</div><div className="muted" style={{ fontSize: 11 }}>{l}</div></div>
        ))}</div>
      </Link>
    ))}</div>
  );
}

/** Growth at a glance for the first site, as on the reference dashboard. */
function SeoStrip() {
  const { data } = useLoad(async () => {
    const sites = await seo.get<any[]>("/sites").catch(() => []);
    return sites.length ? { site: sites[0], dash: await seo.get(`/sites/${sites[0].id}/dashboard`).catch(() => null) } : null;
  });
  if (!data?.dash) return null;
  const d = data.dash;
  const tiles: [string, string, string][] = [
    ["Organic clicks · 28d", d.organic.has_data ? d.organic.clicks.toLocaleString() : "—", d.organic.has_data ? `${d.organic.clicks_growth_pct > 0 ? "+" : ""}${d.organic.clicks_growth_pct}%` : "not connected"],
    ["Top-10 keywords", d.rankings.has_data ? `${d.rankings.top_10}/${d.rankings.tracked}` : "—", ""],
    ["AI-answer visibility", d.ai_visibility.has_data ? String(d.ai_visibility.overall.score) : "—", ""],
    ["Authority", d.authority.has_data ? String(d.authority.latest?.authority_score ?? "—") : "—", ""],
    ["Technical health", d.technical.has_data ? String(d.technical.health_score) : "—", d.technical.has_data ? `${d.technical.open_issues?.critical || 0} critical` : "crawler not connected"],
  ];
  return (
    <div className="card mb">
      <div className="row between"><h3>SEO & GEO growth · {data.site.domain}</h3><Link href="/marketing/seo"><button className="sm ghost">Open growth overview</button></Link></div>
      <div className="grid mt" style={{ gridTemplateColumns: "repeat(5, minmax(0, 1fr))" }}>{tiles.map(([l, v, sub]) => (
        <div key={l} className="kpi" style={{ background: "var(--panel-2)", borderRadius: 8, padding: "10px 12px" }}><div className="l">{l}</div><div className="n" style={{ fontSize: 20 }}>{v}</div><div className="d">{sub}</div></div>
      ))}</div>
    </div>
  );
}

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Email open rate by weekday × hour sent — from tracked opens of real deliveries. */
export function SendTimes() {
  const { data } = useLoad(() => mk.sendTimes());
  if (!data) return null;
  const cell = (d: number, h: number) => data.cells.find((c) => c.weekday === d && c.hour === h);
  const max = Math.max(0.01, ...data.cells.map((c) => c.open_rate));
  return (
    <div className="card">
      <div className="row between"><h3>Best send times</h3><span className="muted" style={{ fontSize: 12 }}>email open rate · {data.timezone}</span></div>
      {data.note ? <div className="empty">{data.note}</div> : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "34px repeat(24, minmax(0, 1fr))", gap: 2, marginTop: 10, fontSize: 10 }}>
            <span />{Array.from({ length: 24 }, (_, h) => <span key={h} className="muted" style={{ textAlign: "center" }}>{h % 6 === 0 ? h : ""}</span>)}
            {DAYS.map((day, d) => [
              <span key={day} className="muted">{day}</span>,
              ...Array.from({ length: 24 }, (_, h) => {
                const c = cell(d, h);
                return <span key={`${d}-${h}`} title={c ? `${day} ${h}:00 — ${Math.round(c.open_rate * 100)}% of ${c.sent}` : `${day} ${h}:00 — no sends`} style={{ height: 14, borderRadius: 2, background: c ? `rgba(214,169,58,${0.15 + 0.85 * (c.open_rate / max)})` : "var(--panel-2)" }} />;
              }),
            ])}
          </div>
          <div className="muted mt" style={{ fontSize: 12 }}>{data.best.length ? `Best: ${data.best.map((b) => `${DAYS[b.weekday]} ${b.hour}:00 (${Math.round(b.open_rate * 100)}%)`).join(" · ")}` : "Best slots need at least 20 sends in a slot."} · {data.messages.toLocaleString()} tracked emails</div>
        </>
      )}
    </div>
  );
}
