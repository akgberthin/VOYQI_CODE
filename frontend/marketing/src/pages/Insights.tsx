import { useState } from "react";
import { Link } from "react-router-dom";
import { useSession } from "@shared/session";
import { Empty, ErrorNote, Loading, PageHead, StatusBadge, useAction, useLoad } from "@shared/ui";
import { Ch } from "../bits";
import { CHANNELS, CHANNEL_LABEL, mk, money, pct } from "../mk";

// ── Audience: segments + suppression list ─────────────────────────────
export function Audience() {
  const run = useAction();
  const { me } = useSession();
  const segments = useLoad(() => mk.segments());
  const supp = useLoad(() => mk.suppression());
  const [seg, setSeg] = useState({ name: "", rules: [{ field: "city", value: "" }] as { field: string; value: string }[] });
  const [s, setS] = useState({ channel: "email", contact_identifier: "", reason: "opt_out" });
  const [check, setCheck] = useState<{ contact: string; result: string } | null>(null);
  const [openSeg, setOpenSeg] = useState<string | null>(null);
  const criteria = Object.fromEntries(seg.rules.filter((r) => r.field && r.value).map((r) => [r.field, r.value]));
  return (
    <>
      <PageHead title="Audience" text="Segments decide who a campaign reaches. The suppression list decides who it never reaches — opt-outs, bounces and complaints are blocked on that channel with no override (CAN-SPAM, TCPA)." />
      <div className="split">
        <div>
          <div className="card mb">
            <h3>New segment</h3>
            <input className="mt" style={{ width: "100%" }} placeholder="Segment name, e.g. Austin EV-curious renters" value={seg.name} onChange={(e) => setSeg({ ...seg, name: e.target.value })} />
            {seg.rules.map((r, i) => (
              <div key={i} className="row mt">
                <select value={r.field} onChange={(e) => setSeg({ ...seg, rules: seg.rules.map((x, j) => (j === i ? { ...x, field: e.target.value } : x)) })}>
                  {["city", "member_tier", "vehicle_class", "last_rental_days", "subscription_status", "channel_opt_in"].map((f) => <option key={f}>{f}</option>)}
                </select>
                <input placeholder="value" value={r.value} onChange={(e) => setSeg({ ...seg, rules: seg.rules.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)) })} />
                <button className="sm ghost" onClick={() => setSeg({ ...seg, rules: seg.rules.filter((_, j) => j !== i) })}>×</button>
              </div>
            ))}
            <div className="row mt">
              <button className="sm ghost" onClick={() => setSeg({ ...seg, rules: [...seg.rules, { field: "city", value: "" }] })}>+ rule</button>
              <button disabled={!seg.name} onClick={() => run("Segment created", () => mk.createSegment({ name: seg.name, filter_criteria: criteria, created_by: me?.email })).then(() => { setSeg({ name: "", rules: [{ field: "city", value: "" }] }); segments.reload(); })}>Create segment</button>
            </div>
          </div>
          {segments.loading ? <Loading /> : !(segments.data || []).length ? <Empty>No segments.</Empty> : (
            <div className="card table-wrap"><table>
              <thead><tr><th>Segment</th><th>Rules</th><th>Members</th></tr></thead>
              <tbody>{(segments.data || []).map((x) => (
                <tr key={x.id}>
                  <td><b>{x.name}</b><div className="muted">{x.created_by}</div></td>
                  <td>{Object.entries(x.filter_criteria).map(([k, v]) => <span key={k} className="badge" style={{ marginRight: 4 }}>{k}: {String(v)}</span>)}</td>
                  <td><button className="sm ghost" onClick={() => setOpenSeg(openSeg === x.id ? null : x.id)}>{x.member_count.toLocaleString()} · contacts</button></td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
          {openSeg && <Contacts segmentId={openSeg} name={(segments.data || []).find((x) => x.id === openSeg)?.name || ""} onChange={segments.reload} />}
        </div>
        <div>
          <div className="card mb">
            <h3>Suppression list</h3>
            <div className="form mt">
              <label className="field">Channel<select value={s.channel} onChange={(e) => setS({ ...s, channel: e.target.value })}>{CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></label>
              <label className="field">Email / phone<input value={s.contact_identifier} onChange={(e) => setS({ ...s, contact_identifier: e.target.value })} /></label>
              <label className="field">Reason<select value={s.reason} onChange={(e) => setS({ ...s, reason: e.target.value })}><option value="opt_out">opt out</option><option value="bounce">bounce</option><option value="complaint">complaint</option></select></label>
            </div>
            <div className="row mt">
              <button disabled={!s.contact_identifier} onClick={() => run("Suppressed", () => mk.suppress(s)).then(() => { setS({ ...s, contact_identifier: "" }); supp.reload(); })}>Suppress</button>
              <button className="ghost" disabled={!s.contact_identifier} onClick={async () => { const r = await mk.checkSuppressed(s.channel, s.contact_identifier); setCheck({ contact: s.contact_identifier, result: r.suppressed ? `suppressed (${r.reason})` : "can be contacted" }); }}>Check</button>
            </div>
            {check && <div className={`notice ${check.result.startsWith("supp") ? "bad" : "ok"} mt`}>{check.contact} on {s.channel}: {check.result}</div>}
          </div>
          <div className="card table-wrap">
            {supp.loading ? <Loading /> : !(supp.data || []).length ? <Empty>Nobody suppressed.</Empty> : (
              <table><thead><tr><th>Contact</th><th>Channel</th><th>Reason</th><th /></tr></thead>
                <tbody>{(supp.data || []).map((x) => (
                  <tr key={x.id}><td className="mono">{x.contact_identifier}</td><td><Ch c={x.channel} /></td><td>{x.reason.replace("_", " ")}</td>
                    <td><button className="sm ghost" onClick={() => confirm(`Allow messages to ${x.contact_identifier} again? Only do this if they re-consented.`) && run("Removed", () => mk.unsuppress(x.id, me?.email || "marketing")).then(supp.reload)}>Remove</button></td></tr>
                ))}</tbody></table>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

function Contacts({ segmentId, name, onChange }: { segmentId: string; name: string; onChange: () => void }) {
  const run = useAction();
  const members = useLoad(() => mk.members(segmentId), [segmentId]);
  const [text, setText] = useState("");
  const parse = () => text.split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((p) => p.some(Boolean)).map(([n = "", a = "", b = ""]) => {
    const parts = [a, b];
    return { name: n, email: parts.find((x) => x.includes("@")) || "", phone: parts.find((x) => /^\+?[\d\s()-]{7,}$/.test(x)) || "" };
  });
  const rows = members.data || [];
  return (
    <div className="card mt">
      <h3>Contacts in “{name}”</h3>
      <div className="sub">One per line: <span className="mono">name, email, phone</span> (either contact may be left out). Duplicates are skipped.</div>
      <textarea className="mt" style={{ minHeight: 90 }} placeholder={"Ava Patel, ava@example.com, +15125550101\nNoah Kim, noah@example.com"} value={text} onChange={(e) => setText(e.target.value)} />
      <button className="mt" disabled={!text.trim()} onClick={() => run("Contacts imported", async () => { const r = await mk.addMembers(segmentId, parse()); setText(""); members.reload(); onChange(); return r; })}>Import</button>
      {members.loading ? <Loading /> : !rows.length ? <Empty>No contacts yet.</Empty> : (
        <table className="mt"><tbody>{rows.slice(0, 200).map((m) => (
          <tr key={m.id}><td>{m.name || <span className="muted">—</span>}</td><td className="mono">{m.email}</td><td className="mono">{m.phone}</td>
            <td><button className="sm ghost" onClick={() => run("Removed", () => mk.removeMember(segmentId, m.id)).then(() => { members.reload(); onChange(); })}>×</button></td></tr>
        ))}</tbody></table>
      )}
      {rows.length > 200 && <div className="muted mt">Showing 200 of {rows.length}.</div>}
    </div>
  );
}

// ── A/B tests ─────────────────────────────────────────────────────────
export function AbTests() {
  const run = useAction();
  const tests = useLoad(() => mk.abTests());
  const campaigns = useLoad(() => mk.campaigns());
  const [campaignId, setCampaignId] = useState("");
  const drafts = useLoad(() => (campaignId ? mk.drafts(campaignId) : Promise.resolve([])), [campaignId]);
  const [f, setF] = useState({ a: "", b: "", metric: "click_rate" });
  const cname = (id: string) => (campaigns.data || []).find((c) => c.id === id)?.name || id.slice(0, 8);
  const record = (t: any, variant: string) => {
    const sent = prompt(`Variant ${variant.toUpperCase()}: how many more were sent?`, "100");
    if (sent === null) return;
    const conv = prompt(`Variant ${variant.toUpperCase()}: how many of those converted (${t.metric.replace("_", " ")})?`, "10");
    if (conv === null) return;
    run("Recorded", () => mk.recordAbResult(t.id, { variant, sent_delta: Number(sent), converted_delta: Number(conv) })).then(tests.reload);
  };
  return (
    <>
      <PageHead title="A/B tests" text="Two drafts of the same campaign compete. A test concludes only when the difference is statistically significant (p < 0.05, ≥100 per arm) — otherwise it reports insufficient data and keeps running." />
      <div className="card mb">
        <h3>New test</h3>
        <div className="form mt">
          <label className="field">Campaign<select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}><option value="">— choose —</option>{(campaigns.data || []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          {(["a", "b"] as const).map((v) => (
            <label key={v} className="field">Variant {v.toUpperCase()}<select value={f[v]} onChange={(e) => setF({ ...f, [v]: e.target.value })}><option value="">— draft —</option>{(drafts.data || []).map((d) => <option key={d.id} value={d.id}>{(d.subject_line || d.body_text).slice(0, 50)}</option>)}</select></label>
          ))}
          <label className="field">Metric<select value={f.metric} onChange={(e) => setF({ ...f, metric: e.target.value })}><option value="click_rate">click rate</option><option value="open_rate">open rate</option><option value="conversion_rate">conversion rate</option></select></label>
        </div>
        {campaignId && (drafts.data || []).length < 2 && <div className="sub mt">This campaign needs two drafts — <Link to={`/campaigns/${campaignId}`}>write another</Link>.</div>}
        <button className="mt" disabled={!f.a || !f.b || f.a === f.b} onClick={() => run("Test started", () => mk.createAbTest({ campaign_id: campaignId, variant_a_draft_id: f.a, variant_b_draft_id: f.b, metric: f.metric })).then(tests.reload)}>Start test</button>
      </div>
      {tests.loading ? <Loading /> : !(tests.data || []).length ? <Empty>No tests yet.</Empty> : (
        <div className="grid cols-2">{(tests.data || []).map((t) => {
          const ra = t.variant_a_sent ? t.variant_a_converted / t.variant_a_sent : 0;
          const rb = t.variant_b_sent ? t.variant_b_converted / t.variant_b_sent : 0;
          const arm = (v: string, sent: number, conv: number, rate: number, draftId: string) => (
            <div className="card" style={{ background: "var(--panel-2)", borderColor: t.winner_draft_id === draftId ? "var(--ok)" : undefined }}>
              <div className="row between"><b>Variant {v}</b>{t.winner_draft_id === draftId && <span className="badge ok">winner</span>}</div>
              <div className="kpi"><div className="n">{pct(conv, sent)}</div><div className="d">{conv} / {sent}</div></div>
              <div className="bar mt"><div style={{ width: `${Math.min(100, rate * 100 * 3)}%` }} /></div>
              {t.status === "running" && <button className="sm ghost mt" onClick={() => record(t, v.toLowerCase())}>Record results</button>}
            </div>
          );
          return (
            <div key={t.id} className="card">
              <div className="row between"><b>{cname(t.campaign_id)}</b><div className="row"><span className="badge">{t.metric.replace("_", " ")}</span><StatusBadge value={t.status} /></div></div>
              <div className="split mt">{arm("A", t.variant_a_sent, t.variant_a_converted, ra, t.variant_a_draft_id)}{arm("B", t.variant_b_sent, t.variant_b_converted, rb, t.variant_b_draft_id)}</div>
              {t.status !== "concluded" && <button className="mt" onClick={() => run("Evaluated", () => mk.evaluateAbTest(t.id)).then(tests.reload)}>Evaluate significance</button>}
            </div>
          );
        })}</div>
      )}
    </>
  );
}

// ── Calendar ──────────────────────────────────────────────────────────
export function Calendar() {
  const { data, error, loading } = useLoad(() => mk.calendar());
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const start = new Date(month); start.setDate(1 - ((month.getDay() + 6) % 7));
  const days = Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
  const key = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  const byDay: Record<string, any[]> = {};
  for (const e of data || []) { const d = new Date(e.scheduled_at); (byDay[key(d)] ||= []).push(e); }
  const today = key(new Date());
  return (
    <>
      <PageHead title="Calendar" text="Every scheduled send and social post across channels. Entries appear here automatically when something is scheduled.">
        <button className="ghost" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button>
        <b style={{ minWidth: 150, textAlign: "center" }}>{month.toLocaleString(undefined, { month: "long", year: "numeric" })}</b>
        <button className="ghost" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button>
      </PageHead>
      <ErrorNote error={error} />
      {loading ? <Loading /> : (
        <div className="cal">
          {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="dow">{d}</div>)}
          {days.map((d) => (
            <div key={key(d)} className={`day ${d.getMonth() !== month.getMonth() ? "out" : ""} ${key(d) === today ? "today" : ""}`}>
              <div className="num">{d.getDate()}</div>
              {(byDay[key(d)] || []).map((e) => (
                <div key={e.id} className={`ev ch-${e.channel}`} title={`${e.title} · ${new Date(e.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`}>
                  {new Date(e.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} {e.title}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// ── Analytics ─────────────────────────────────────────────────────────
export function Analytics() {
  const { data, error, loading } = useLoad(async () => {
    const [campaigns, snapshots] = await Promise.all([mk.campaigns(), mk.snapshots()]);
    return { campaigns, snapshots };
  });
  if (loading) return <Loading />;
  if (!data) return <ErrorNote error={error} />;
  const rows = data.campaigns.map((c) => {
    const s = data.snapshots.filter((x) => x.campaign_id === c.id);
    const t = (k: "sent_count" | "open_count" | "click_count" | "conversion_count" | "revenue_usd") => s.reduce((a, x) => a + (x[k] || 0), 0);
    return { c, sent: t("sent_count"), open: t("open_count"), click: t("click_count"), conv: t("conversion_count"), rev: t("revenue_usd") };
  }).sort((a, b) => b.rev - a.rev || b.sent - a.sent);
  const tot = rows.reduce((a, r) => ({ sent: a.sent + r.sent, open: a.open + r.open, click: a.click + r.click, conv: a.conv + r.conv, rev: a.rev + r.rev }), { sent: 0, open: 0, click: 0, conv: 0, rev: 0 });
  const channels = CHANNELS.map((ch) => {
    const r = rows.filter((x) => x.c.channel === ch);
    const sum = (k: "sent" | "click" | "conv" | "rev") => r.reduce((a, x) => a + x[k], 0);
    return { ch, sent: sum("sent"), click: sum("click"), conv: sum("conv"), rev: sum("rev") };
  });
  return (
    <>
      <PageHead title="Analytics" text="Delivered counts come from sends; opens, clicks, conversions and revenue from provider results recorded on each campaign." />
      <div className="grid cols-4 mb">
        {[["Delivered", tot.sent.toLocaleString()], ["Open rate", pct(tot.open, tot.sent)], ["Conversion rate", pct(tot.conv, tot.sent)], ["Revenue", money(tot.rev)]].map(([l, v]) => (
          <div key={l} className="card kpi"><div className="l">{l}</div><div className="n">{v}</div></div>
        ))}
      </div>
      <div className="grid cols-4 mb" style={{ gridTemplateColumns: "repeat(5, minmax(0,1fr))" }}>
        {channels.map((x) => (
          <div key={x.ch} className="card"><Ch c={x.ch} /><div className="kpi"><div className="n" style={{ fontSize: 20 }}>{x.sent.toLocaleString()}</div><div className="d">click {pct(x.click, x.sent)} · {money(x.rev)}</div></div></div>
        ))}
      </div>
      {!rows.length ? <Empty>No campaigns.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Campaign</th><th>Channel</th><th>Delivered</th><th>Open</th><th>Click</th><th>Conversion</th><th>Revenue</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.c.id}><td><Link to={`/campaigns/${r.c.id}`}>{r.c.name}</Link></td><td><Ch c={r.c.channel} /></td><td>{r.sent.toLocaleString()}</td><td>{pct(r.open, r.sent)}</td><td>{pct(r.click, r.sent)}</td><td>{pct(r.conv, r.sent)}</td><td>{money(r.rev)}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
