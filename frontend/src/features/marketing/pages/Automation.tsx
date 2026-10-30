"use client";

import { useState } from "react";
import { Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "@/lib/ui";
import { Ch } from "../bits";
import { CHANNEL_LABEL, TRIGGERS, mk } from "../mk";

type Step = { delay_hours: number; channel: string; subject: string; body: string };
const STEP_CHANNELS = ["email", "sms", "push", "voice"];
const triggerLabel = (t: string) => TRIGGERS.find(([k]) => k === t)?.[1] || t;
const when = (h: number) => (h === 0 ? "Immediately" : h % 24 === 0 ? `Day ${h / 24}` : `+${h}h`);

export default function Automation() {
  const run = useAction();
  const list = useLoad(() => mk.journeys());
  const templates = useLoad(() => mk.journeyTemplates());
  const segments = useLoad(() => mk.segments());
  const [open, setOpen] = useState<string | null>(null);
  const fromTemplate = (t: any) => run(`“${t.name}” created — review and activate it`, async () => {
    const j = await mk.createJourney({ name: t.name, trigger_event: t.trigger_event, steps: t.steps });
    setOpen(j.id);
  }).then(list.reload);
  const journeys = list.data || [];
  return (
    <>
      <PageHead title="Automation flows" text="Journeys send a sequence of messages across channels, each after its delay, starting when something happens to a contact (they sign up, become a lead, cancel) or when you enrol them. Every step respects the suppression list, opt-outs, voice consent and the calling window. New flows start paused." />
      <ErrorNote error={list.error} />
      <div className="card mb">
        <h3>Start from a template</h3>
        <div className="row mt">{(templates.data || []).map((t) => (
          <button key={t.key} className="ghost" onClick={() => fromTemplate(t)}>{t.name} <span className="muted">· {t.steps.length} steps</span></button>
        ))}</div>
      </div>
      <div className="grid cols-4 mb">
        {[["Flows", journeys.length], ["Active", journeys.filter((j) => j.status === "active").length],
          ["Contacts in a flow", journeys.reduce((a, j) => a + (j.enrollments.active || 0), 0)],
          ["Completed", journeys.reduce((a, j) => a + (j.enrollments.completed || 0), 0)]].map(([l, v]) => (
          <div key={l as string} className="card kpi"><div className="l">{l}</div><div className="n">{v}</div></div>
        ))}
      </div>
      {list.loading ? <Loading /> : !journeys.length ? <Empty>No flows yet — start from a template.</Empty> : journeys.map((j) => (
        <div key={j.id} className="card mb" style={open === j.id ? { borderColor: "var(--gold)" } : undefined}>
          <div className="row between">
            <div><b>{j.name}</b><div className="muted" style={{ fontSize: 12 }}>Trigger: {triggerLabel(j.trigger_event)} · {j.steps.length} steps</div></div>
            <div className="row">
              <StatusBadge value={j.status} />
              <span className="badge">{j.enrollments.active || 0} active · {j.enrollments.completed || 0} done</span>
              {j.status === "active"
                ? <button className="sm ghost" onClick={() => run("Paused", () => mk.updateJourney(j.id, { status: "paused" })).then(list.reload)}>Pause</button>
                : <button className="sm ok" onClick={() => run("Active — contacts are enrolled as their trigger happens", () => mk.updateJourney(j.id, { status: "active" })).then(list.reload)}>Activate</button>}
              <button className="sm ghost" onClick={() => setOpen(open === j.id ? null : j.id)}>{open === j.id ? "Close" : "Open"}</button>
            </div>
          </div>
          <div className="row mt">{j.steps.map((st: Step, i: number) => (
            <span key={i} className="badge" title={st.body}>{when(st.delay_hours)} · {CHANNEL_LABEL[st.channel]}</span>
          ))}</div>
          {open === j.id && <Editor journey={j} segments={segments.data || []} onChange={list.reload} />}
        </div>
      ))}
    </>
  );
}

function Editor({ journey, segments, onChange }: { journey: any; segments: any[]; onChange: () => void }) {
  const run = useAction();
  const [steps, setSteps] = useState<Step[]>(journey.steps);
  const [name, setName] = useState(journey.name);
  const [trigger, setTrigger] = useState(journey.trigger_event);
  const [segment, setSegment] = useState("");
  const enrollments = useLoad(() => mk.enrollments(journey.id), [journey.id]);
  const set = (i: number, patch: Partial<Step>) => setSteps(steps.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  return (
    <div className="mt">
      <div className="form">
        <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="field">Starts when<select value={trigger} onChange={(e) => setTrigger(e.target.value)}>{TRIGGERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      </div>
      {steps.map((st, i) => (
        <div key={i} className="card mt" style={{ background: "var(--panel-2)" }}>
          <div className="row between"><b>Step {i + 1}</b><button className="sm ghost" disabled={steps.length === 1} onClick={() => setSteps(steps.filter((_, j) => j !== i))}>Remove</button></div>
          <div className="form mt">
            <label className="field">Send after (hours from start)<input type="number" min={0} value={st.delay_hours} onChange={(e) => set(i, { delay_hours: Number(e.target.value) })} /></label>
            <label className="field">Channel<select value={st.channel} onChange={(e) => set(i, { channel: e.target.value })}>{STEP_CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></label>
            {st.channel === "email" && <label className="field">Subject<input value={st.subject} onChange={(e) => set(i, { subject: e.target.value })} /></label>}
          </div>
          <textarea className="mt" style={{ minHeight: 70, fontFamily: "inherit" }} value={st.body} onChange={(e) => set(i, { body: e.target.value })} />
          {st.channel === "voice" && <div className="muted" style={{ fontSize: 12 }}>Calls open by saying they're automated, reach only contacts with voice consent, and wait for the calling window.</div>}
        </div>
      ))}
      <div className="row mt">
        <button className="ghost" onClick={() => setSteps([...steps, { delay_hours: (steps.at(-1)?.delay_hours || 0) + 24, channel: "email", subject: "", body: "" }])}>+ Step</button>
        <button onClick={() => run("Flow saved", () => mk.updateJourney(journey.id, { name, trigger_event: trigger, steps })).then(onChange)}>Save flow</button>
        <span className="muted" style={{ fontSize: 12 }}>{"{first_name}"} is replaced with each contact's name.</span>
      </div>
      <div className="row mt">
        <select value={segment} onChange={(e) => setSegment(e.target.value)}><option value="">Enrol a segment…</option>{segments.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.member_count})</option>)}</select>
        <button className="ghost" disabled={!segment} onClick={() => run("Enrolled", async () => mk.enroll(journey.id, { segment_id: segment })).then(() => { enrollments.reload(); onChange(); })}>Enrol</button>
      </div>
      <h4 className="mt">Contacts in this flow</h4>
      {enrollments.loading ? <Loading /> : !(enrollments.data || []).length ? <Empty>Nobody yet.</Empty> : (
        <div className="table-wrap"><table>
          <thead><tr><th>Contact</th><th>Status</th><th>Next step</th><th>History</th><th /></tr></thead>
          <tbody>{(enrollments.data || []).slice(0, 100).map((e) => (
            <tr key={e.id}>
              <td>{e.name || "—"}<div className="muted mono">{e.email || e.phone}</div><div className="muted" style={{ fontSize: 11 }}>{e.source}</div></td>
              <td><StatusBadge value={e.status} /></td>
              <td>{e.status === "active" ? <>step {e.current_step + 1} · {fmtTime(e.next_run_at)}</> : "—"}</td>
              <td style={{ maxWidth: 360 }}>{e.history.map((h: any, i: number) => <div key={i} style={{ fontSize: 12 }}><Ch c={h.channel} /> <span className={String(h.outcome).startsWith("sent") ? "" : "muted"}>{h.outcome}</span></div>)}</td>
              <td>{e.status === "active" && <button className="sm ghost" onClick={() => run("Stopped", () => mk.stopEnrollment(e.id)).then(enrollments.reload)}>Stop</button>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}
