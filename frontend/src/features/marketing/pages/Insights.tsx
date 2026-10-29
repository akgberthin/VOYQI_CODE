"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "@/lib/session";
import { Empty, ErrorNote, Loading, PageHead, StatusBadge, useAction, useLoad } from "@/lib/ui";
import { Ch } from "../bits";
import { CHANNELS, CHANNEL_LABEL, mk, money, pct, type Estimate } from "../mk";

// ── Audience: builder, segments, contacts, suppression ────────────────
type Rule = { field: string; values: string[]; min: string; max: string };
const isNum = (v: string) => v.trim() !== "" && !Number.isNaN(Number(v));
const FIELD_LABEL: Record<string, string> = {
  channel_opt_in: "Reachable on", city: "City", class: "Mobility class", status: "Member status", tenure_months: "Tenure (months)",
  churn_risk: "Churn risk (0–100)", upgrade_ready: "Upgrade readiness", gu_balance: "GU balance", hub: "Home hub", service_level: "Service level",
  trip_frequency: "Trips per month", last_trip_days: "Days since last trip", age_range: "Age range", income: "Income bracket", life_stage: "Life stage",
};
const label = (f: string) => FIELD_LABEL[f] || f.replace(/_/g, " ");

function toCriteria(rules: Rule[], numeric: (f: string) => boolean): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const r of rules) {
    if (!r.field) continue;
    if (r.field !== "channel_opt_in" && numeric(r.field)) {
      const range: Record<string, number> = {};
      if (isNum(r.min)) range.min = Number(r.min);
      if (isNum(r.max)) range.max = Number(r.max);
      if (Object.keys(range).length) out[r.field] = range;
    } else if (r.values.length) out[r.field] = r.field === "channel_opt_in" ? r.values : r.values.length === 1 ? r.values[0] : r.values;
  }
  return out;
}

export function Audience() {
  const run = useAction();
  const router = useRouter();
  const { me } = useSession();
  const segments = useLoad(() => mk.segments());
  const attrs = useLoad(() => mk.attributes());
  const supp = useLoad(() => mk.suppression());
  const [rules, setRules] = useState<Rule[]>([]);
  const [name, setName] = useState("");
  const [est, setEst] = useState<Estimate | null>(null);
  const [s, setS] = useState({ channel: "email", contact_identifier: "", reason: "opt_out" });
  const [check, setCheck] = useState<{ contact: string; result: string } | null>(null);
  const [openSeg, setOpenSeg] = useState<string | null>(null);
  const values = attrs.data || {};
  const numeric = (f: string) => (values[f] || []).length > 0 && (values[f] || []).every(isNum);
  const criteria = toCriteria(rules, numeric);
  const key = JSON.stringify(criteria);
  // Re-estimate shortly after the rules stop changing.
  useEffect(() => {
    const t = setTimeout(() => { mk.estimate(criteria).then(setEst).catch(() => setEst(null)); }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, segments.data]);
  const fields = ["channel_opt_in", ...Object.keys(values)];
  const setRule = (i: number, patch: Partial<Rule>) => setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const reloadAll = () => { segments.reload(); attrs.reload(); };
  const use = (segId: string, segName: string) => run("Campaign created for this segment", async () => {
    const c = await mk.createCampaign({ name: `${segName} campaign`, channel: "email", audience_segment_id: segId, created_by: me?.email } as any);
    router.push(`/marketing/campaigns/${c.id}`);
  });
  const total = (segments.data || []).reduce((a, x) => a + x.member_count, 0);
  const largest = (segments.data || []).slice().sort((a, b) => b.member_count - a.member_count)[0];
  return (
    <>
      <PageHead title="Audience" text="Build an audience from what you know about your contacts, see how many it reaches on each channel, and save it as a segment. The suppression list decides who is never reached — opt-outs, bounces and complaints are blocked on that channel with no override (CAN-SPAM, TCPA)." />
      <div className="grid cols-4 mb">
        <div className="card kpi"><div className="l">Contacts</div><div className="n">{(est?.of ?? 0).toLocaleString()}</div><div className="d">across every segment</div></div>
        <div className="card kpi"><div className="l">Segments</div><div className="n">{(segments.data || []).length}</div><div className="d">{total.toLocaleString()} memberships</div></div>
        <div className="card kpi"><div className="l">Largest segment</div><div className="n" style={{ fontSize: 18 }}>{largest?.name || "—"}</div><div className="d">{largest ? `${largest.member_count.toLocaleString()} contacts` : ""}</div></div>
        <div className="card kpi"><div className="l">Suppressed</div><div className="n">{(supp.data || []).length}</div><div className="d">never contacted on that channel</div></div>
      </div>
      <div className="split mb">
        <div className="card">
          <h3>Audience builder</h3>
          {!fields.slice(1).length && <div className="sub mt">Contacts have no attributes yet — import them with columns such as city, class, status, tenure_months or churn_risk (below, under a segment) to target by them.</div>}
          {rules.map((r, i) => (
            <div key={i} className="card mt" style={{ background: "var(--panel-2)", padding: 10 }}>
              <div className="row between">
                <select value={r.field} onChange={(e) => setRule(i, { field: e.target.value, values: [], min: "", max: "" })}>
                  <option value="">— attribute —</option>{fields.map((f) => <option key={f} value={f}>{label(f)}</option>)}
                </select>
                <button className="sm ghost" onClick={() => setRules(rules.filter((_, j) => j !== i))}>Remove</button>
              </div>
              {r.field && (r.field !== "channel_opt_in" && numeric(r.field) ? (
                <div className="row mt">
                  <input type="number" style={{ width: 110 }} placeholder={`min (${values[r.field][0]})`} value={r.min} onChange={(e) => setRule(i, { min: e.target.value })} />
                  <span className="muted">to</span>
                  <input type="number" style={{ width: 110 }} placeholder={`max (${values[r.field].at(-1)})`} value={r.max} onChange={(e) => setRule(i, { max: e.target.value })} />
                </div>
              ) : (
                <div className="row mt">{(r.field === "channel_opt_in" ? ["email", "sms", "push", "voice"] : values[r.field] || []).map((v) => (
                  <label key={v} className="badge" style={{ cursor: "pointer" }}>
                    <input type="checkbox" checked={r.values.includes(v)} onChange={(e) => setRule(i, { values: e.target.checked ? [...r.values, v] : r.values.filter((x) => x !== v) })} /> {v}
                  </label>
                ))}</div>
              ))}
            </div>
          ))}
          <div className="row mt">
            <button className="sm ghost" onClick={() => setRules([...rules, { field: "", values: [], min: "", max: "" }])}>+ rule</button>
            {rules.length > 0 && <button className="sm ghost" onClick={() => setRules([])}>Clear</button>}
          </div>
          <div className="sub mt">All rules must match. Several ticked values in one rule match any of them.</div>
        </div>
        <div className="card">
          <h3>Estimated audience</h3>
          <div className="kpi mt"><div className="n" style={{ fontSize: 40 }}>{(est?.total ?? 0).toLocaleString()}</div><div className="d">{est && est.of ? `${Math.round((est.total / est.of) * 100)}% of ${est.of.toLocaleString()} contacts` : "no contacts yet"}</div></div>
          <div className="grid cols-2 mt">{(["email", "sms", "push", "voice"] as const).map((c) => (
            <div key={c}><div className="row between"><Ch c={c} /><b>{(est?.reachable[c] ?? 0).toLocaleString()}</b></div><div className="bar mt" style={{ marginTop: 4 }}><div style={{ width: `${est?.total ? ((est.reachable[c] / est.total) * 100) : 0}%` }} /></div></div>
          ))}</div>
          <div className="muted mt" style={{ fontSize: 12 }}>Voice counts only contacts with consent to automated calls. Suppressed contacts are removed at send time.</div>
          <div className="row mt">
            <input style={{ flex: 1 }} placeholder="Segment name, e.g. Miami luxury, low churn" value={name} onChange={(e) => setName(e.target.value)} />
            <button disabled={!name || !est?.total} onClick={() => run("Segment saved with its contacts", () => mk.createSegment({ name, filter_criteria: criteria, created_by: me?.email, populate: true })).then((ok) => { if (ok) { setName(""); reloadAll(); } })}>Save as segment</button>
          </div>
        </div>
      </div>
      <div className="card mb table-wrap">
        <div className="row between"><h3>Segments</h3>
          <button className="sm ghost" onClick={() => run("Empty segment created — import contacts into it", () => mk.createSegment({ name: `Import ${new Date().toLocaleDateString()}`, filter_criteria: {}, created_by: me?.email })).then(reloadAll)}>New empty segment</button>
        </div>
        {segments.loading ? <Loading /> : !(segments.data || []).length ? <Empty>No segments yet — create an empty one and import contacts.</Empty> : (
          <table className="mt">
            <thead><tr><th>Segment</th><th>Rules</th><th>Contacts</th><th /></tr></thead>
            <tbody>{(segments.data || []).map((x) => (
              <tr key={x.id}>
                <td><b>{x.name}</b><div className="muted">{x.created_by}</div></td>
                <td>{Object.keys(x.filter_criteria).length ? Object.entries(x.filter_criteria).map(([k, v]) => <span key={k} className="badge" style={{ marginRight: 4 }}>{label(k)}: {typeof v === "object" && !Array.isArray(v) ? `${(v as any).min ?? "…"}–${(v as any).max ?? "…"}` : Array.isArray(v) ? v.join(" / ") : String(v)}</span>) : <span className="muted">imported list</span>}</td>
                <td>{x.member_count.toLocaleString()}</td>
                <td><div className="row">
                  <button className="sm ghost" onClick={() => setOpenSeg(openSeg === x.id ? null : x.id)}>{openSeg === x.id ? "Close" : "Contacts"}</button>
                  <button className="sm" disabled={!x.member_count} onClick={() => use(x.id, x.name)}>Use</button>
                </div></td>
              </tr>
            ))}</tbody>
          </table>
        )}
        {openSeg && <Contacts segmentId={openSeg} name={(segments.data || []).find((x) => x.id === openSeg)?.name || ""} onChange={reloadAll} />}
      </div>
      <div className="split">
        <CallingWindow />
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

function CallingWindow() {
  const run = useAction();
  const { data, reload } = useLoad(() => mk.settings());
  const [f, setF] = useState<any>(null);
  if (data && !f) setF(data);
  if (!f) return null;
  return (
    <div className="card mb">
      <div className="row between"><h3>Calling window</h3><span className={`badge ${data?.voice_window_open_now ? "ok" : "warn"}`}>{data?.voice_window_open_now ? "open now" : "closed now"}</span></div>
      <div className="sub">AI voice calls are placed only between these hours (local time); outside them, scheduled calls wait for the next window. 08:00-21:00 is the legal limit.</div>
      <div className="form mt">
        <label className="field">Timezone<input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} /></label>
        <label className="field">From (hour)<input type="number" min={8} max={21} value={f.voice_window_start} onChange={(e) => setF({ ...f, voice_window_start: Number(e.target.value) })} /></label>
        <label className="field">Until (hour)<input type="number" min={8} max={21} value={f.voice_window_end} onChange={(e) => setF({ ...f, voice_window_end: Number(e.target.value) })} /></label>
      </div>
      <button className="mt" onClick={() => run("Calling window saved", () => mk.saveSettings({ timezone: f.timezone, voice_window_start: f.voice_window_start, voice_window_end: f.voice_window_end })).then(reload)}>Save</button>
    </div>
  );
}

/** CSV with a header row: name, email, phone, push_token, voice (yes for
    written consent to automated calls) — any other column is an attribute. */
export function parseContacts(text: string) {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return [];
  const head = lines[0].toLowerCase().split(",").map((h) => h.trim().replace(/\s+/g, "_"));
  const hasHeader = head.some((h) => ["email", "phone", "name"].includes(h));
  const cols = hasHeader ? head : ["name", "email", "phone", "voice"];
  return (hasHeader ? lines.slice(1) : lines).map((line) => {
    const cells = line.split(",").map((x) => x.trim());
    const m: any = { attributes: {} };
    cols.forEach((c, i) => {
      const v = cells[i] ?? "";
      if (["name", "email", "phone", "push_token"].includes(c)) m[c] = v;
      else if (c === "voice" || c === "consent_voice") m.consent_voice = /^(voice|yes|y|true|1)$/i.test(v);
      else if (v) m.attributes[c] = isNum(v) ? Number(v) : v;
    });
    // Without a header, a phone may sit in the email column and vice versa.
    if (!hasHeader && m.email && !m.email.includes("@")) [m.email, m.phone] = [m.phone || "", m.email];
    return m;
  });
}

function Contacts({ segmentId, name, onChange }: { segmentId: string; name: string; onChange: () => void }) {
  const run = useAction();
  const members = useLoad(() => mk.members(segmentId), [segmentId]);
  const [text, setText] = useState("");
  const parsed = parseContacts(text);
  const rows = members.data || [];
  return (
    <div className="card mt" style={{ background: "var(--panel-2)" }}>
      <h3>Contacts in “{name}”</h3>
      <div className="sub">CSV with a header row. Known columns: <span className="mono">name, email, phone, push_token, voice</span> (put <span className="mono">yes</span> under voice only for written consent to automated calls). Every other column — <span className="mono">city, class, status, tenure_months, churn_risk</span>… — becomes something you can target and personalise by. Duplicates are skipped.</div>
      <textarea className="mt" style={{ minHeight: 90 }} placeholder={"name,email,phone,voice,city,class,churn_risk\nAva Patel,ava@example.com,+15125550101,yes,Miami,luxury,20"} value={text} onChange={(e) => setText(e.target.value)} />
      {parsed.length > 0 && <div className="muted" style={{ fontSize: 12 }}>{parsed.length} contact(s){parsed[0] && Object.keys(parsed[0].attributes).length ? ` · attributes: ${Object.keys(parsed[0].attributes).join(", ")}` : ""}</div>}
      <button className="mt" disabled={!parsed.length} onClick={() => run("Contacts imported", async () => { const r = await mk.addMembers(segmentId, parsed); setText(""); members.reload(); onChange(); return r; })}>Import</button>
      {members.loading ? <Loading /> : !rows.length ? <Empty>No contacts yet.</Empty> : (
        <table className="mt"><tbody>{rows.slice(0, 200).map((m) => (
          <tr key={m.id}><td>{m.name || <span className="muted">—</span>}</td><td className="mono">{m.email}</td><td className="mono">{m.phone}</td>
            <td>{Object.entries(m.attributes || {}).map(([k, v]) => <span key={k} className="badge" style={{ marginRight: 3 }}>{k}: {String(v)}</span>)}</td>
            <td>{m.consent_voice && <span className="badge ok" title="Consented to automated calls">voice ✓</span>}</td>
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
  const allDrafts = useLoad(() => mk.drafts());
  const draft = (id: string) => (allDrafts.data || []).find((d) => d.id === id);
  const variable = (t: any) => { const a = draft(t.variant_a_draft_id), b = draft(t.variant_b_draft_id); if (!a || !b) return ""; return a.subject_line !== b.subject_line && a.body_text === b.body_text ? "subject line" : a.subject_line === b.subject_line ? "message copy" : "subject and copy"; };
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
        {campaignId && (drafts.data || []).length < 2 && <div className="sub mt">This campaign needs two drafts — <Link href={`/marketing/campaigns/${campaignId}`}>write another</Link>.</div>}
        <button className="mt" disabled={!f.a || !f.b || f.a === f.b} onClick={() => run("Test started", () => mk.createAbTest({ campaign_id: campaignId, variant_a_draft_id: f.a, variant_b_draft_id: f.b, metric: f.metric })).then(tests.reload)}>Start test</button>
      </div>
      {tests.loading ? <Loading /> : !(tests.data || []).length ? <Empty>No tests yet.</Empty> : (
        <div className="grid cols-2">{(tests.data || []).map((t) => {
          const ra = t.variant_a_sent ? t.variant_a_converted / t.variant_a_sent : 0;
          const rb = t.variant_b_sent ? t.variant_b_converted / t.variant_b_sent : 0;
          const arm = (v: string, sent: number, conv: number, rate: number, draftId: string) => (
            <div className="card" style={{ background: "var(--panel-2)", borderColor: t.winner_draft_id === draftId ? "var(--ok)" : undefined }}>
              <div className="row between"><b>Variant {v}</b>{t.winner_draft_id === draftId && <span className="badge ok">winner</span>}</div>
              <div className="muted clip" style={{ fontSize: 12 }} title={draft(draftId)?.subject_line || draft(draftId)?.body_text}>{draft(draftId)?.subject_line || draft(draftId)?.body_text?.slice(0, 60)}</div>
              <div className="kpi"><div className="n">{pct(conv, sent)}</div><div className="d">{conv} / {sent}</div></div>
              <div className="bar mt"><div style={{ width: `${Math.min(100, rate * 100 * 3)}%` }} /></div>
              {t.status === "running" && <button className="sm ghost mt" onClick={() => record(t, v.toLowerCase())}>Record results</button>}
            </div>
          );
          return (
            <div key={t.id} className="card">
              <div className="row between"><b>{cname(t.campaign_id)}</b><div className="row">{variable(t) && <span className="badge info">tests {variable(t)}</span>}<span className="badge">{t.metric.replace("_", " ")}</span><StatusBadge value={t.status} /></div></div>
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
const RANGES: [number, string][] = [[7, "Last 7 days"], [30, "Last 30 days"], [90, "Last 90 days"], [0, "All time"]];

export function Analytics() {
  const [days, setDays] = useState(30);
  const { data, error, loading } = useLoad(async () => {
    const [campaigns, snapshots, report] = await Promise.all([mk.campaigns(), mk.snapshots(), mk.report(days || 365)]);
    return { campaigns, snapshots, report };
  }, [days]);
  if (loading && !data) return <Loading />;
  if (!data) return <ErrorNote error={error} />;
  const since = days ? Date.now() - days * 86400e3 : 0;
  const snaps = data.snapshots.filter((x) => !since || !x.snapshot_at || new Date(x.snapshot_at).getTime() >= since);
  const rows = data.campaigns.map((c) => {
    const s = snaps.filter((x) => x.campaign_id === c.id);
    const t = (k: "sent_count" | "open_count" | "click_count" | "conversion_count" | "revenue_usd") => s.reduce((a, x) => a + (x[k] || 0), 0);
    return { c, sent: t("sent_count"), open: t("open_count"), click: t("click_count"), conv: t("conversion_count"), rev: t("revenue_usd") };
  }).filter((r) => r.sent || r.open || r.conv).sort((a, b) => b.rev - a.rev || b.sent - a.sent);
  const tot = rows.reduce((a, r) => ({ sent: a.sent + r.sent, open: a.open + r.open, click: a.click + r.click, conv: a.conv + r.conv, rev: a.rev + r.rev }), { sent: 0, open: 0, click: 0, conv: 0, rev: 0 });
  const channels = CHANNELS.map((ch) => {
    const r = rows.filter((x) => x.c.channel === ch);
    const sum = (k: "sent" | "open" | "click" | "conv" | "rev") => r.reduce((a, x) => a + x[k], 0);
    return { ch, sent: sum("sent"), open: sum("open"), click: sum("click"), conv: sum("conv"), rev: sum("rev") };
  });
  const funnel: [string, number][] = [["Delivered", tot.sent], ["Opened", tot.open], ["Clicked", tot.click], ["Converted", tot.conv]];
  const daily = data.report.daily_delivered as { date: string; delivered: number }[];
  const maxDay = Math.max(1, ...daily.map((d) => d.delivered));
  return (
    <>
      <PageHead title="Analytics" text="Delivered counts come from sends (real deliveries only); opens, clicks, conversions and revenue from provider results recorded on each campaign.">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{RANGES.map(([d, l]) => <option key={d} value={d}>{l}</option>)}</select>
      </PageHead>
      <div className="grid cols-4 mb">
        {[["Delivered", tot.sent.toLocaleString()], ["Open rate", pct(tot.open, tot.sent)], ["Click-through", pct(tot.click, tot.sent)], ["Conversions", `${tot.conv.toLocaleString()} · ${money(tot.rev)}`]].map(([l, v]) => (
          <div key={l} className="card kpi"><div className="l">{l}</div><div className="n">{v}</div></div>
        ))}
      </div>
      <div className="split mb">
        <div className="card">
          <h3>Delivered per day</h3>
          {!daily.length ? <Empty>No sends in this period.</Empty> : (
            <div className="row mt" style={{ alignItems: "flex-end", gap: 4, height: 150, flexWrap: "nowrap" }}>{daily.map((d) => (
              <div key={d.date} title={`${d.date}: ${d.delivered}`} style={{ flex: 1, minWidth: 6, display: "grid", gap: 4, justifyItems: "center" }}>
                <div style={{ width: "100%", height: Math.max(3, (d.delivered / maxDay) * 120), background: "var(--gold)", borderRadius: 3 }} />
                <span className="muted" style={{ fontSize: 9 }}>{d.date.slice(5)}</span>
              </div>
            ))}</div>
          )}
        </div>
        <div className="card">
          <h3>Conversion funnel</h3>
          <div className="grid mt" style={{ gap: 10 }}>{funnel.map(([l, n], i) => (
            <div key={l}>
              <div className="row between" style={{ fontSize: 13 }}><span>{l}</span><span><b>{n.toLocaleString()}</b> <span className="muted">{i ? pct(n, funnel[i - 1][1]) + " of previous" : ""}</span></span></div>
              <div className="bar" style={{ marginTop: 4, height: 12 }}><div style={{ width: `${tot.sent ? (n / tot.sent) * 100 : 0}%` }} /></div>
            </div>
          ))}</div>
        </div>
      </div>
      <div className="card mb table-wrap">
        <h3>Performance by channel</h3>
        <table className="mt"><thead><tr><th>Channel</th><th>Delivered</th><th>Open</th><th>Click</th><th>Conversions</th><th>Revenue</th></tr></thead>
          <tbody>{channels.filter((x) => x.sent || x.conv).map((x) => <tr key={x.ch}><td><Ch c={x.ch} /></td><td>{x.sent.toLocaleString()}</td><td>{pct(x.open, x.sent)}</td><td>{pct(x.click, x.sent)}</td><td>{x.conv}</td><td>{money(x.rev)}</td></tr>)}</tbody></table>
        {!channels.some((x) => x.sent || x.conv) && <Empty>No results in this period.</Empty>}
      </div>
      {!rows.length ? <Empty>No campaign results in this period.</Empty> : (
        <div className="card table-wrap"><h3>Campaigns</h3><table className="mt">
          <thead><tr><th>Campaign</th><th>Channel</th><th>Delivered</th><th>Open</th><th>Click</th><th>Conversion</th><th>Revenue</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.c.id}><td><Link href={`/marketing/campaigns/${r.c.id}`}>{r.c.name}</Link></td><td><Ch c={r.c.channel} /></td><td>{r.sent.toLocaleString()}</td><td>{pct(r.open, r.sent)}</td><td>{pct(r.click, r.sent)}</td><td>{pct(r.conv, r.sent)}</td><td>{money(r.rev)}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
