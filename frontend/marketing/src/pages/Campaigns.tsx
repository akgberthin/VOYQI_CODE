import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useSession } from "@shared/session";
import { Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "@shared/ui";
import { Ch, DraftCard } from "../bits";
import { CHANNELS, CHANNEL_LABEL, RECURRENCE, inHours, mk, money, pct, toIso, type Draft } from "../mk";

export function Campaigns() {
  const run = useAction();
  const nav = useNavigate();
  const { me } = useSession();
  const [channel, setChannel] = useState("");
  const list = useLoad(() => mk.campaigns(channel || undefined), [channel]);
  const segments = useLoad(() => mk.segments());
  const [form, setForm] = useState({ name: "", channel: "email", objective: "", audience_segment_id: "" });
  const create = () => run("Campaign created", async () => {
    const c = await mk.createCampaign({ ...form, audience_segment_id: form.audience_segment_id || null, created_by: me?.email } as any);
    nav(`/campaigns/${c.id}`);
  });
  return (
    <>
      <PageHead title="Campaigns" text="A campaign is one channel, one objective and (optionally) one audience segment. Its content, sends and results live on its page.">
        <select value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">all channels</option>{CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select>
      </PageHead>
      <div className="card mb">
        <h3>New campaign</h3>
        <div className="form mt">
          <label className="field">Name<input value={form.name} placeholder="Spring EV subscription push" onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field">Channel<select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>{CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></label>
          <label className="field">Audience<select value={form.audience_segment_id} onChange={(e) => setForm({ ...form, audience_segment_id: e.target.value })}><option value="">everyone</option>{(segments.data || []).map((s) => <option key={s.id} value={s.id}>{s.name} ({s.member_count})</option>)}</select></label>
          <label className="field" style={{ gridColumn: "1 / -1" }}>Objective / brief<input value={form.objective} placeholder="Convert rental customers in Austin to monthly EV subscriptions" onChange={(e) => setForm({ ...form, objective: e.target.value })} /></label>
        </div>
        <button className="mt" disabled={!form.name} onClick={create}>Create campaign</button>
      </div>
      <ErrorNote error={list.error} />
      {list.loading ? <Loading /> : !(list.data || []).length ? <Empty>No campaigns yet.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Campaign</th><th>Channel</th><th>Objective</th><th>Status</th><th>Owner</th></tr></thead>
          <tbody>{(list.data || []).map((c) => (
            <tr key={c.id} className="click" onClick={() => nav(`/campaigns/${c.id}`)}>
              <td><b>{c.name}</b></td><td><Ch c={c.channel} /></td><td className="muted" style={{ maxWidth: 380 }}>{c.objective}</td><td><StatusBadge value={c.status} /></td><td className="muted">{c.created_by}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}

const NEXT: Record<string, [string, string, string][]> = {
  draft: [["active", "Activate", "ok"]],
  active: [["paused", "Pause", "ghost"], ["completed", "Complete", "ghost"]],
  paused: [["active", "Resume", "ok"], ["completed", "Complete", "ghost"]],
  completed: [],
};

export function CampaignDetail() {
  const { id = "" } = useParams();
  const run = useAction();
  const { me } = useSession();
  const actor = me?.email || "marketing";
  const data = useLoad(async () => {
    const [campaign, drafts, sends, summary, notes, leads, segments] = await Promise.all([
      mk.campaign(id), mk.drafts(id), mk.sends(id), mk.summary(id), mk.notes(id), mk.leads(id), mk.segments(),
    ]);
    return { campaign, drafts, sends, summary, notes, leads, segments };
  }, [id]);
  const [tab, setTab] = useState("content");
  const [manual, setManual] = useState({ subject_line: "", body_text: "" });
  const [brief, setBrief] = useState("");
  const [busyAI, setBusyAI] = useState(false);
  const [sched, setSched] = useState<{ draft: Draft | null; at: string; recurrence: string }>({ draft: null, at: inHours(24), recurrence: "none" });
  const [recipients, setRecipients] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");

  if (data.loading && !data.data) return <Loading />;
  if (!data.data) return <ErrorNote error={data.error} />;
  const { campaign: c, drafts, sends, summary, notes, leads, segments } = data.data;
  const reload = data.reload;
  const segment = segments.find((s) => s.id === c.audience_segment_id);

  const aiDraft = async () => {
    setBusyAI(true);
    await run("AI draft ready — guardrail applied", () => mk.aiDraft(c.id, brief || c.objective));
    setBusyAI(false);
    reload();
  };
  const schedule = async () => {
    if (!sched.draft) return;
    const at = toIso(sched.at);
    const clash = await mk.conflicts(c.channel, at).catch(() => []);
    if (clash.length && !confirm(`${clash.length} other ${c.channel} send(s) are within the spacing window:\n${clash.map((e) => `• ${e.title} @ ${new Date(e.scheduled_at).toLocaleString()}`).join("\n")}\n\nSchedule anyway?`)) return;
    await run("Send scheduled", () => mk.scheduleSend({ campaign_id: c.id, content_draft_id: sched.draft!.id, channel: c.channel, scheduled_at: at, recurrence: sched.recurrence }));
    setSched({ draft: null, at: inHours(24), recurrence: "none" });
    reload();
  };
  const execute = (sendId: string) => {
    const list = (recipients[sendId] || "").split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
    if (!list.length && !segment) return alert("This campaign has no audience segment — add contacts, or type recipients.");
    if (!confirm(list.length ? `Send now to ${list.length} recipient(s)?` : `Send now to the ${segment!.member_count} contacts in “${segment!.name}”?`)) return;
    const consent = c.channel === "voice" && list.length > 0
      ? confirm("Automated calls need prior express consent (TCPA). Confirm every number listed has given it?")
      : false;
    if (c.channel === "voice" && list.length > 0 && !consent) return;
    run("Sent — every recipient checked against the suppression list", () => mk.executeSend(sendId, list, consent)).then(reload);
  };

  return (
    <>
      <PageHead title={c.name} text={<><Link to="/campaigns">Campaigns</Link> / {c.objective || "No objective set"}</>}>
        <Ch c={c.channel} /><StatusBadge value={c.status} />
        {(NEXT[c.status] || []).map(([to, label, kind]) => <button key={to} className={kind} onClick={() => run(`Campaign ${to}`, () => mk.setStatus(c.id, to)).then(reload)}>{label}</button>)}
      </PageHead>
      <div className="grid cols-4 mb">
        <div className="card kpi"><div className="l">Audience</div><div className="n" style={{ fontSize: 18 }}>{segment ? segment.name : "Everyone"}</div><div className="d">{segment ? `${segment.member_count.toLocaleString()} members` : "no segment"}</div></div>
        <div className="card kpi"><div className="l">Delivered</div><div className="n">{summary.total_sent.toLocaleString()}</div><div className="d">open {pct(summary.total_open, summary.total_sent)} · click {pct(summary.total_click, summary.total_sent)}</div></div>
        <div className="card kpi"><div className="l">Conversions</div><div className="n">{summary.total_conversion}</div><div className="d">{money(summary.total_revenue_usd)}</div></div>
        <div className="card kpi"><div className="l">Content</div><div className="n">{drafts.length}</div><div className="d">{drafts.filter((d) => d.status === "held_for_review").length} held for review</div></div>
      </div>
      <div className="tabs">
        {[["content", "Content"], ["sends", `Sends (${sends.length})`], ["results", "Results"], ["notes", `Notes (${notes.length})`], ["leads", `Leads (${leads.length})`]].map(([k, l]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {tab === "content" && (
        <>
          <div className="split mb">
            <div className="card">
              <h3>✦ AI draft</h3>
              <div className="sub">The Marketing Content Agent writes {CHANNEL_LABEL[c.channel]} copy for this campaign. It passes the same brand and compliance guardrail as anything a person writes.</div>
              <textarea className="mt" style={{ minHeight: 80 }} placeholder={c.objective || "What should this message achieve?"} value={brief} onChange={(e) => setBrief(e.target.value)} />
              <button className="mt" disabled={busyAI} onClick={aiDraft}>{busyAI ? "Drafting…" : "Draft with AI"}</button>
            </div>
            <div className="card">
              <h3>Write it yourself</h3>
              {c.channel === "email" && <input className="mt" style={{ width: "100%" }} placeholder="Subject line" value={manual.subject_line} onChange={(e) => setManual({ ...manual, subject_line: e.target.value })} />}
              <textarea className="mt" style={{ minHeight: 80 }} placeholder="Message" value={manual.body_text} onChange={(e) => setManual({ ...manual, body_text: e.target.value })} />
              <div className={`counter ${c.channel === "sms" && manual.body_text.length > 320 ? "over" : ""}`}>{manual.body_text.length}{c.channel === "sms" ? " / 320" : ""} characters</div>
              <button className="mt" disabled={!manual.body_text} onClick={() => run("Draft saved — guardrail checked", () => mk.createDraft({ campaign_id: c.id, channel: c.channel, ...manual, created_by: actor } as any)).then(() => { setManual({ subject_line: "", body_text: "" }); reload(); })}>Save draft</button>
            </div>
          </div>
          {!drafts.length ? <Empty>No content yet.</Empty> : (
            <div className="grid cols-2">{drafts.map((d) => (
              <DraftCard key={d.id} d={d} onChange={reload} actions={d.status === "approved" && c.channel !== "social" && <button className="sm" onClick={() => setSched({ ...sched, draft: d })}>Schedule send</button>} />
            ))}</div>
          )}
          {sched.draft && (
            <div className="card mt" style={{ borderColor: "var(--gold)" }}>
              <h3>Schedule “{(sched.draft.subject_line || sched.draft.body_text).slice(0, 60)}”</h3>
              <div className="row mt">
                <input type="datetime-local" value={sched.at} onChange={(e) => setSched({ ...sched, at: e.target.value })} />
                <select value={sched.recurrence} onChange={(e) => setSched({ ...sched, recurrence: e.target.value })}>{RECURRENCE.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
                <button onClick={schedule}>Schedule</button><button className="ghost" onClick={() => setSched({ ...sched, draft: null })}>Cancel</button>
              </div>
              <div className="sub mt">Checks the calendar for other {c.channel} sends too close to this one (over-messaging guard).</div>
            </div>
          )}
          {c.channel === "social" && <div className="notice info mt">Social content is published from <Link to="/social">Social</Link>.</div>}
        </>
      )}

      {tab === "sends" && c.status !== "active" && sends.some((s) => s.status === "scheduled") && (
        <div className="notice warn mb">This campaign is {c.status}: its scheduled sends wait until it's activated (Send now still works).</div>
      )}
      {tab === "sends" && (!sends.length ? <Empty>Nothing scheduled. Approve a draft and schedule it from the Content tab.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>When</th><th>Status</th><th>Reached</th><th>Not reached</th><th>Send now</th></tr></thead>
          <tbody>{sends.map((s) => (
            <tr key={s.id}>
              <td>{fmtTime(s.scheduled_at)}{s.recurrence !== "none" && <div><span className="badge info">{RECURRENCE.find(([k]) => k === s.recurrence)?.[1]}</span></div>}{s.sent_at && <div className="muted">ran {fmtTime(s.sent_at)}</div>}</td>
              <td><StatusBadge value={s.status} /></td>
              <td>{s.status === "scheduled" ? "—" : <>
                <div>{s.recipients_targeted - s.recipients_suppressed - s.recipients_failed - s.recipients_console} delivered</div>
                {s.recipients_console > 0 && <div className="muted" title="No provider is configured for this channel in Integrations, so these were only logged">{s.recipients_console} console only</div>}
              </>}</td>
              <td>{s.status === "scheduled" ? "—" : <>
                {s.recipients_suppressed > 0 && <div className="muted">{s.recipients_suppressed} suppressed</div>}
                {s.recipients_failed > 0 && <div style={{ color: "var(--bad)" }}>{s.recipients_failed} failed</div>}
                {s.failure_detail && <div className="muted" style={{ fontSize: 12, maxWidth: 320 }}>{s.failure_detail}</div>}
              </>}</td>
              <td>{s.status === "scheduled" && (
                <div className="row">
                  <input style={{ minWidth: 220 }} placeholder={segment ? `${segment.name} (${segment.member_count})` : c.channel === "email" ? "a@x.com, b@y.com" : "+15125550101, …"} value={recipients[s.id] || ""} onChange={(e) => setRecipients({ ...recipients, [s.id]: e.target.value })} />
                  <button className="sm" onClick={() => execute(s.id)}>Send now</button>
                </div>
              )}</td>
            </tr>
          ))}</tbody>
        </table><div className="sub mt">Scheduled sends go out automatically at their time to the campaign's segment. Every recipient is checked against the suppression list first — no override exists. Delivery uses the providers set up in super admin → Integrations; without one, messages are only logged (“console only”) and not counted as reach.</div></div>
      ))}

      {tab === "results" && <Results campaignId={c.id} channel={c.channel} onChange={reload} summary={summary} />}

      {tab === "notes" && (
        <div className="card">
          <div className="row"><input style={{ flex: 1 }} placeholder="Add a note for the team" value={note} onChange={(e) => setNote(e.target.value)} />
            <button disabled={!note} onClick={() => run("Note added", () => mk.addNote({ author: actor, body: note, campaign_id: c.id })).then(() => { setNote(""); reload(); })}>Add</button></div>
          <table className="mt"><tbody>{notes.map((n) => (
            <tr key={n.id}><td>{n.body}</td><td className="muted">{n.author}</td><td><button className="sm ghost" onClick={() => run("Note deleted", () => mk.deleteNote(n.id)).then(reload)}>Delete</button></td></tr>
          ))}</tbody></table>
        </div>
      )}

      {tab === "leads" && (!leads.length ? <Empty>No leads attributed to this campaign.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Name</th><th>Contact</th><th>Landing page</th><th>Consent</th><th>When</th></tr></thead>
          <tbody>{leads.map((l) => <tr key={l.id}><td>{l.name}</td><td>{l.email}<div className="muted">{l.phone}</div></td><td className="muted">{l.landing_page}</td><td>{l.consent_marketing ? "yes" : "no"}</td><td className="muted">{fmtTime(l.created_at)}</td></tr>)}</tbody>
        </table></div>
      ))}
    </>
  );
}

function Results({ campaignId, channel, summary, onChange }: { campaignId: string; channel: string; summary: any; onChange: () => void }) {
  const run = useAction();
  const snaps = useLoad(() => mk.snapshots(campaignId), [campaignId]);
  const [f, setF] = useState({ sent_count: "", open_count: "", click_count: "", conversion_count: "", revenue_usd: "" });
  const num = (v: string) => Number(v || 0);
  return (
    <>
      <div className="grid cols-4 mb">
        {[["Sent", summary.total_sent], ["Open rate", pct(summary.total_open, summary.total_sent)], ["Click rate", pct(summary.total_click, summary.total_sent)], ["Conversion rate", pct(summary.total_conversion, summary.total_sent)]].map(([l, v]) => (
          <div key={l} className="card kpi"><div className="l">{l}</div><div className="n">{v}</div></div>
        ))}
      </div>
      <div className="card mb">
        <h3>Record provider results</h3>
        <div className="sub">Opens, clicks and conversions reported by the {channel} provider. Delivered counts are recorded automatically on send.</div>
        <div className="form mt">
          {(["sent_count", "open_count", "click_count", "conversion_count", "revenue_usd"] as const).map((k) => (
            <label key={k} className="field">{k.replace(/_/g, " ")}<input type="number" value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>
          ))}
        </div>
        <button className="mt" onClick={() => run("Results recorded", () => mk.recordSnapshot({ campaign_id: campaignId, channel: channel as any, sent_count: num(f.sent_count), open_count: num(f.open_count), click_count: num(f.click_count), conversion_count: num(f.conversion_count), revenue_usd: num(f.revenue_usd) })).then(() => { setF({ sent_count: "", open_count: "", click_count: "", conversion_count: "", revenue_usd: "" }); snaps.reload(); onChange(); })}>Record</button>
      </div>
      {snaps.data && snaps.data.length > 0 && (
        <div className="card table-wrap"><table>
          <thead><tr><th>Sent</th><th>Opens</th><th>Clicks</th><th>Conversions</th><th>Revenue</th></tr></thead>
          <tbody>{snaps.data.map((s) => <tr key={s.id}><td>{s.sent_count}</td><td>{s.open_count}</td><td>{s.click_count}</td><td>{s.conversion_count}</td><td>{money(s.revenue_usd)}</td></tr>)}</tbody>
        </table></div>
      )}
    </>
  );
}
