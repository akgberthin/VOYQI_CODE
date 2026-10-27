import { useEffect, useState } from "react";
import { Link, NavLink, useParams } from "react-router-dom";
import { useSession } from "@shared/session";
import { PageHead, useAction, useLoad } from "@shared/ui";
import { DraftCard } from "../bits";
import { CHANNEL_ICON, CHANNEL_LABEL, RECURRENCE, inHours, mk, toIso, type Draft } from "../mk";

const LIMIT: Record<string, number> = { sms: 320, push: 178 };
const HINT: Record<string, string> = {
  email: "Subject line and body. The guardrail holds subjects over 150 characters and absolute claims (“guaranteed”, “risk-free”…).",
  sms: "Up to 320 characters (two segments). Opted-out numbers are never messaged.",
  push: "Short — about 178 characters shows fully on most phones.",
  voice: "The script an outbound voice call reads. Write it the way it should sound.",
};

export default function Compose() {
  const { channel = "email" } = useParams();
  const run = useAction();
  const { me } = useSession();
  const actor = me?.email || "marketing";
  const campaigns = useLoad(() => mk.campaigns(channel), [channel]);
  const library = useLoad(() => mk.library(channel), [channel]);
  const [campaignId, setCampaignId] = useState("");
  const [newName, setNewName] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<Draft | null>(null);
  const [at, setAt] = useState(inHours(24));
  const [recurrence, setRecurrence] = useState("none");

  useEffect(() => { setCampaignId(""); setSaved(null); setSubject(""); setBody(""); }, [channel]);
  useEffect(() => { if (!campaignId && campaigns.data?.length) setCampaignId(campaigns.data[0].id); }, [campaigns.data, campaignId]);

  const limit = LIMIT[channel];
  async function ensureCampaign(): Promise<string> {
    if (campaignId) return campaignId;
    const c = await mk.createCampaign({ name: newName || `${CHANNEL_LABEL[channel]} message`, channel: channel as any, objective: brief, created_by: actor } as any);
    setCampaignId(c.id);
    campaigns.reload();
    return c.id;
  }
  const save = () => run("Saved — guardrail checked", async () => {
    const id = await ensureCampaign();
    setSaved(await mk.createDraft({ campaign_id: id, channel: channel as any, subject_line: subject, body_text: body, created_by: actor } as any));
  });
  const ai = async () => {
    setBusy(true);
    await run("AI draft ready", async () => {
      const id = await ensureCampaign();
      const r = await mk.aiDraft(id, brief);
      if (r.draft) { setSaved(r.draft); setSubject(r.draft.subject_line || ""); setBody(r.draft.body_text || ""); }
    });
    setBusy(false);
  };
  const schedule = () => run("Scheduled — on the calendar", async () => {
    await mk.scheduleSend({ campaign_id: saved!.campaign_id, content_draft_id: saved!.id, channel, scheduled_at: toIso(at), recurrence });
    setSaved({ ...saved!, status: "scheduled" });
  });
  const toLibrary = () => run("Saved to library", () => mk.saveToLibrary({ title: subject || body.slice(0, 40), channel, body_text: subject ? `${subject}\n\n${body}` : body, tags: [], created_by: actor })).then(library.reload);

  return (
    <>
      <PageHead title={`${CHANNEL_ICON[channel]} ${CHANNEL_LABEL[channel]} composer`} text={HINT[channel]}>
        {["email", "sms", "push", "voice"].map((c) => <NavLink key={c} to={`/compose/${c}`} className={({ isActive }) => `btn ${isActive ? "" : "ghost"}`} style={{ textDecoration: "none" }}>{CHANNEL_LABEL[c]}</NavLink>)}
      </PageHead>
      <div className="composer">
        <div className="card">
          <div className="form">
            <label className="field">Campaign
              <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
                <option value="">+ new {CHANNEL_LABEL[channel]} campaign</option>
                {(campaigns.data || []).map((c) => <option key={c.id} value={c.id}>{c.name} ({c.status})</option>)}
              </select>
            </label>
            {!campaignId && <label className="field">New campaign name<input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Weekend luxury rentals" /></label>}
            {(library.data || []).length > 0 && (
              <label className="field">Start from template
                <select value="" onChange={(e) => { const t = library.data!.find((x) => x.id === e.target.value); if (t) { const [first, ...rest] = t.body_text.split("\n\n"); if (channel === "email" && rest.length) { setSubject(first); setBody(rest.join("\n\n")); } else setBody(t.body_text); } }}>
                  <option value="">— choose —</option>{library.data!.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
                </select>
              </label>
            )}
          </div>
          {channel === "email" && <input className="mt" style={{ width: "100%" }} placeholder="Subject line" value={subject} onChange={(e) => setSubject(e.target.value)} />}
          <textarea className="mt" style={{ minHeight: 200, fontFamily: "inherit", fontSize: 14 }} placeholder={channel === "voice" ? "Hi, this is VOYQI…" : "Write your message"} value={body} onChange={(e) => setBody(e.target.value)} />
          <div className={`counter ${limit && body.length > limit ? "over" : ""}`}>{body.length}{limit ? ` / ${limit}` : ""} characters{channel === "email" && subject ? ` · subject ${subject.length}/150` : ""}</div>
          <div className="row mt">
            <button disabled={!body} onClick={save}>Save draft</button>
            <button className="ghost" disabled={!body} onClick={toLibrary}>Save as template</button>
          </div>
          <div className="card mt" style={{ background: "var(--panel-2)" }}>
            <h3>✦ Or let the AI write it</h3>
            <div className="row mt">
              <input style={{ flex: 1 }} placeholder="Brief: who it's for and what it should achieve" value={brief} onChange={(e) => setBrief(e.target.value)} />
              <button disabled={busy} onClick={ai}>{busy ? "Drafting…" : "Draft with AI"}</button>
            </div>
          </div>
        </div>
        <div className="grid">
          <div className="card">
            <h3>Preview</h3>
            <div className="mt">
              {channel === "email" ? (
                <div className="preview"><div className="subj">{subject || "Subject line"}</div>{body || "Your message…"}</div>
              ) : (
                <div className="phone"><div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>{channel === "voice" ? "📞 Call script" : channel === "push" ? "🔔 VOYQI · now" : "💬 VOYQI"}</div><div className="bubble">{body || "Your message…"}</div></div>
              )}
            </div>
          </div>
          {saved && (
            <>
              <DraftCard d={saved} onChange={async () => setSaved(await mk.drafts(saved.campaign_id).then((ds) => ds.find((d) => d.id === saved.id) || saved))} />
              {saved.status === "approved" && (
                <div className="card">
                  <h3>Schedule</h3>
                  <div className="row mt"><input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /><select value={recurrence} onChange={(e) => setRecurrence(e.target.value)}>{RECURRENCE.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select><button onClick={schedule}>Schedule send</button></div>
                </div>
              )}
              {saved.status === "scheduled" && <div className="notice ok">Scheduled. Send it to recipients from the <Link to={`/campaigns/${saved.campaign_id}`}>campaign page</Link>.</div>}
            </>
          )}
        </div>
      </div>
    </>
  );
}
