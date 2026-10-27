import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "@shared/ui";
import { CLASSES, TONES, mk } from "../mk";

export default function Autopilot() {
  const run = useAction();
  const cfg = useLoad(() => mk.autopilot());
  const accounts = useLoad(() => mk.accounts());
  const slots = useLoad(() => mk.slots());
  const drafts = useLoad(async () => (await mk.drafts()).filter((d) => d.channel === "social"));
  const [f, setF] = useState<any>(null);
  const [planning, setPlanning] = useState(false);
  useEffect(() => { if (cfg.data && !f) setF(cfg.data); }, [cfg.data, f]);
  const text = (id: string | null) => (drafts.data || []).find((d) => d.id === id)?.body_text || "";
  const acct = (id: string) => (accounts.data || []).find((a) => a.id === id);
  const reloadAll = () => { slots.reload(); drafts.reload(); cfg.reload(); };
  const plan = async () => { setPlanning(true); await run("Planned", async () => { const r = await mk.planNow(); return r; }); setPlanning(false); reloadAll(); };
  if (!f) return <Loading />;
  const connected = (accounts.data || []).filter((a) => a.connected);
  const toggle = (id: string) => setF({ ...f, account_ids: f.account_ids.includes(id) ? f.account_ids.filter((x: string) => x !== id) : [...f.account_ids, id] });
  return (
    <>
      <PageHead title="◎ Social autopilot" text="The Marketing Content Agent plans the coming week of posts for your accounts — copy through the brand guardrail and, with OpenAI connected, an image. In Suggest mode every post waits for you; in Auto-publish mode clean posts are scheduled straight away. Anything the guardrail flags always waits for a person.">
        <button className="ghost" disabled={planning || !f.enabled} onClick={plan}>{planning ? "Planning…" : "Plan now"}</button>
      </PageHead>
      <ErrorNote error={cfg.error} />
      <div className="card mb">
        <div className="row between"><h3>Settings</h3><label className="row" style={{ gap: 6 }}><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> <b>{f.enabled ? "On" : "Off"}</b></label></div>
        <div className="form mt">
          <label className="field">Mode<select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="suggest">Suggest — I approve each post</option><option value="autopublish">Auto-publish clean posts</option></select></label>
          <label className="field">Posts per week (per account)<input type="number" min={1} max={21} value={f.posts_per_week} onChange={(e) => setF({ ...f, posts_per_week: Number(e.target.value) })} /></label>
          <label className="field">Posting hour (UTC)<input type="number" min={0} max={23} value={f.post_hour_utc} onChange={(e) => setF({ ...f, post_hour_utc: Number(e.target.value) })} /></label>
          <label className="field">Featured class<select value={f.mobility_class} onChange={(e) => setF({ ...f, mobility_class: e.target.value })}><option value="">any</option>{CLASSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="field">Tone<select value={f.tone} onChange={(e) => setF({ ...f, tone: e.target.value })}>{TONES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          <label className="field">Images<select value={String(f.include_images)} onChange={(e) => setF({ ...f, include_images: e.target.value === "true" })}><option value="true">generate with OpenAI</option><option value="false">text only</option></select></label>
        </div>
        <label className="field mt">Themes — one per line, rotated through the week<textarea style={{ minHeight: 80, fontFamily: "inherit" }} value={f.themes} placeholder={"Miami Brickell hub opening\nWeekend luxury availability\nRefer a friend, earn GU"} onChange={(e) => setF({ ...f, themes: e.target.value })} /></label>
        <div className="mt"><div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>Accounts (none selected = all connected)</div>
          <div className="row">{connected.map((a) => <label key={a.id} className="badge" style={{ cursor: "pointer" }}><input type="checkbox" checked={f.account_ids.includes(a.id)} onChange={() => toggle(a.id)} /> {a.platform} {a.handle}</label>)}
            {!connected.length && <span className="muted">No connected accounts — <Link to="/social">connect one</Link>.</span>}</div>
        </div>
        <div className="row mt">
          <button onClick={() => run("Autopilot saved", () => mk.saveAutopilot(f)).then(reloadAll)}>Save</button>
          {cfg.data?.last_planned_at && <span className="muted">Last planned {fmtTime(cfg.data.last_planned_at)} · replans every 12 hours</span>}
        </div>
      </div>
      <h3>Planned posts</h3>
      {slots.loading ? <Loading /> : !(slots.data || []).length ? <Empty>Nothing planned yet{f.enabled ? " — press Plan now." : " — turn autopilot on."}</Empty> : (
        <div className="grid cols-3">{(slots.data || []).map((s) => {
          const a = acct(s.social_account_id);
          return (
            <div key={s.id} className="card" style={s.status === "held" ? { borderColor: "var(--warn)" } : undefined}>
              <div className="row between"><b>{fmtTime(s.planned_at)}</b><StatusBadge value={s.status === "held" ? "held for review" : s.status} /></div>
              <div className="muted mt" style={{ fontSize: 12 }}>{a ? `${a.platform} ${a.handle}` : ""} · {s.theme}</div>
              {s.image_url && <img src={s.image_url} alt="" style={{ width: "100%", aspectRatio: "1", objectFit: "cover", borderRadius: 8, marginTop: 8 }} />}
              <div className="mt" style={{ whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{text(s.content_draft_id)}</div>
              {s.status === "held" && <div className="notice warn mt">The guardrail flagged this copy — approving it is your review.</div>}
              {["proposed", "held"].includes(s.status) && (
                <div className="row mt">
                  <button className="sm ok" onClick={() => run("Scheduled", () => mk.approveSlot(s.id)).then(reloadAll)}>Approve</button>
                  <button className="sm ghost" onClick={() => run("Rejected", () => mk.rejectSlot(s.id)).then(reloadAll)}>Reject</button>
                </div>
              )}
              {s.decided_by && <div className="muted mt" style={{ fontSize: 12 }}>by {s.decided_by}</div>}
            </div>
          );
        })}</div>
      )}
    </>
  );
}
