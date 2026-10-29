"use client";

import Link from "next/link";
import { ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useLoad } from "@/lib/ui";
import { Ch } from "../bits";
import { CHANNELS, money, mk, pct } from "../mk";

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
