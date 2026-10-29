"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { NavLink } from "@/components/NavLink";
import { useSession } from "@/lib/session";
import { PageHead, useAction, useLoad } from "@/lib/ui";
import { DraftCard } from "../bits";
import { CHANNEL_ICON, CHANNEL_LABEL, DEEP_LINKS, RECURRENCE, TOKENS, VOICES, inHours, mk, smsParts, toIso, type Draft } from "../mk";

const HINT: Record<string, string> = {
  email: "Sender, subject, inbox preview and body — with an optional B subject that starts an A/B test. The guardrail holds subjects over 150 characters and absolute claims (“guaranteed”, “risk-free”…).",
  sms: "Up to 320 characters. The opt-out line is added unless the message already says STOP; opted-out numbers are never messaged.",
  push: "A title and a short body, opening a screen in the app when tapped.",
  voice: "The script an AI voice reads on an outbound call. It always opens by saying the call is automated, reaches only contacts with voice consent, and waits for the calling window.",
};
const LIMIT: Record<string, number> = { sms: 320, push: 120 };
const STOP_LINE = "Reply STOP to opt out.";
const EMPTY = { from_name: "", reply_to: "", preview_text: "", append_stop: true, sender_id: "", media_url: "", deep_link: "", notification_type: "promotional", platforms: [] as string[], voice: "Polly.Joanna" };
const PLATFORMS: [string, string][] = [["ios", "iOS (APNs)"], ["android", "Android (FCM)"], ["web", "Web push"]];

export default function Compose() {
  const { channel = "email" } = useParams<{ channel: string }>();
  const run = useAction();
  const { me } = useSession();
  const actor = me?.email || "marketing";
  const campaigns = useLoad(() => mk.campaigns(channel), [channel]);
  const library = useLoad(() => mk.library(channel), [channel]);
  const assets = useLoad(() => (channel === "sms" ? mk.assets() : Promise.resolve([])), [channel]);
  const policy = useLoad(() => mk.settings());
  const [campaignId, setCampaignId] = useState("");
  const [newName, setNewName] = useState("");
  const [subject, setSubject] = useState("");
  const [subjectB, setSubjectB] = useState("");
  const [body, setBody] = useState("");
  const [o, setO] = useState(EMPTY);
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<Draft | null>(null);
  const [abNote, setAbNote] = useState("");
  const [at, setAt] = useState(inHours(24));
  const [recurrence, setRecurrence] = useState("none");
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { setCampaignId(""); setSaved(null); setSubject(""); setSubjectB(""); setBody(""); setO(EMPTY); setAbNote(""); }, [channel]);
  useEffect(() => { if (!campaignId && campaigns.data?.length) setCampaignId(campaigns.data[0].id); }, [campaigns.data, campaignId]);

  /** The options this channel sends with every delivery. */
  const options = (): Record<string, unknown> => {
    if (channel === "email") return { from_name: o.from_name, reply_to: o.reply_to, preview_text: o.preview_text };
    if (channel === "sms") return { append_stop: o.append_stop, sender_id: o.sender_id, media_url: o.media_url };
    if (channel === "push") return { deep_link: o.deep_link, notification_type: o.notification_type, ...(o.platforms.length && o.platforms.length < 3 ? { platforms: o.platforms } : {}) };
    if (channel === "voice") return { voice: o.voice, language: VOICES.find(([k]) => k === o.voice)?.[2] };
    return {};
  };
  const insertToken = (t: string) => {
    const el = bodyRef.current, token = `{${t}}`;
    if (!el) return setBody(body + token);
    const [a, b] = [el.selectionStart ?? body.length, el.selectionEnd ?? body.length];
    setBody(body.slice(0, a) + token + body.slice(b));
    requestAnimationFrame(() => { el.focus(); el.setSelectionRange(a + token.length, a + token.length); });
  };
  async function ensureCampaign(): Promise<string> {
    if (campaignId) return campaignId;
    const c = await mk.createCampaign({ name: newName || `${CHANNEL_LABEL[channel]} message`, channel: channel as any, objective: brief, created_by: actor } as any);
    setCampaignId(c.id);
    campaigns.reload();
    return c.id;
  }
  const save = () => run(subjectB ? "Saved both variants — A/B test started" : "Saved — guardrail checked", async () => {
    const id = await ensureCampaign();
    const a = await mk.createDraft({ campaign_id: id, channel: channel as any, subject_line: subject, body_text: body, options: options(), created_by: actor } as any);
    setSaved(a);
    setAbNote("");
    if (channel === "email" && subjectB.trim()) {
      const b = await mk.createDraft({ campaign_id: id, channel: "email", subject_line: subjectB, body_text: body, options: options(), created_by: actor } as any);
      await mk.createAbTest({ campaign_id: id, variant_a_draft_id: a.id, variant_b_draft_id: b.id, metric: "open_rate" });
      setAbNote(b.status === "approved" ? "Variant B saved too. Schedule each variant to half the audience from the campaign page; record results under A/B tests." : "Variant B was held by the guardrail — review it before testing.");
    }
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

  const smsOut = channel === "sms" && o.append_stop && !/stop/i.test(body) ? `${body.trim()} ${STOP_LINE}`.trim() : body;
  const parts = smsParts(smsOut);
  const limit = LIMIT[channel];
  const sample = (t: string) => t.replace(/\{first_name\}/g, "Ava").replace(/\{name\}/g, "Ava Patel").replace(/\{city\}/g, "Miami").replace(/\{class\}/g, "Luxury").replace(/\{hub\}/g, "Brickell").replace(/\{vehicle\}/g, "Model Y").replace(/\{gu_balance\}/g, "1,240");

  return (
    <>
      <PageHead title={`${CHANNEL_ICON[channel]} ${CHANNEL_LABEL[channel]} composer`} text={HINT[channel]}>
        {["email", "sms", "push", "voice"].map((c) => <NavLink key={c} href={`/marketing/compose/${c}`} className="btn" activeClassName="" inactiveClassName="ghost" style={{ textDecoration: "none" }}>{CHANNEL_LABEL[c]}</NavLink>)}
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

          {channel === "email" && (
            <>
              <div className="form mt">
                <label className="field">From name<input value={o.from_name} placeholder="VOYQI Concierge" onChange={(e) => setO({ ...o, from_name: e.target.value })} /></label>
                <label className="field">Reply-to<input value={o.reply_to} placeholder="care@voyqi.com" onChange={(e) => setO({ ...o, reply_to: e.target.value })} /></label>
              </div>
              <label className="field mt">Subject line<input value={subject} onChange={(e) => setSubject(e.target.value)} /><span className={subject.length > 150 ? "counter over" : "counter"}>{subject.length}/150</span></label>
              <label className="field">A/B variant subject (optional)<input value={subjectB} placeholder="A second subject to test against the first — 50/50 split" onChange={(e) => setSubjectB(e.target.value)} /></label>
              <label className="field">Preview text<input value={o.preview_text} placeholder="The line inboxes show after the subject" onChange={(e) => setO({ ...o, preview_text: e.target.value })} /><span className={o.preview_text.length > 140 ? "counter over" : "counter"}>{o.preview_text.length}/140</span></label>
            </>
          )}
          {channel === "sms" && (
            <>
              <div className="form mt">
                <label className="field">Sender ID (optional)<input value={o.sender_id} maxLength={16} placeholder="VOYQI — or leave blank for your number" onChange={(e) => setO({ ...o, sender_id: e.target.value })} /></label>
                <label className="field">MMS image<select value={o.media_url} onChange={(e) => setO({ ...o, media_url: e.target.value })}><option value="">No — SMS only</option>{(assets.data || []).filter((a) => a.url.startsWith("https://")).map((a) => <option key={a.id} value={a.url}>{a.title || a.id.slice(0, 8)}</option>)}</select></label>
              </div>
              <div className="muted" style={{ fontSize: 12 }}>Alphanumeric sender IDs aren&apos;t allowed by US carriers. MMS images must be publicly reachable over https (images uploaded in AI studio are).
                {policy.data && <> Texts go out {policy.data.sms_window_start}:00–{policy.data.sms_window_end}:00 {policy.data.timezone} — outside that they wait (quiet hours, set under Audience).</>}</div>
            </>
          )}
          {channel === "push" && (
            <div className="form mt">
              <label className="field">Title<input value={subject} maxLength={65} onChange={(e) => setSubject(e.target.value)} /><span className={subject.length > 50 ? "counter over" : "counter"}>{subject.length}/50</span></label>
              <label className="field">Opens<select value={o.deep_link} onChange={(e) => setO({ ...o, deep_link: e.target.value })}>{DEEP_LINKS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
              <label className="field">Type<select value={o.notification_type} onChange={(e) => setO({ ...o, notification_type: e.target.value })}><option value="promotional">Promotional</option><option value="transactional">Transactional</option><option value="ai_proactive">AI proactive</option></select></label>
              <div className="field" style={{ gridColumn: "1 / -1" }}>Platforms
                <div className="row">{PLATFORMS.map(([k, l]) => <label key={k} className="badge" style={{ cursor: "pointer" }}><input type="checkbox" checked={!o.platforms.length || o.platforms.includes(k)} onChange={(e) => { const cur = o.platforms.length ? o.platforms : PLATFORMS.map(([x]) => x); setO({ ...o, platforms: e.target.checked ? [...cur, k] : cur.filter((x) => x !== k) }); }} /> {l}</label>)}</div>
                <span className="muted" style={{ fontSize: 11 }}>Narrowing reaches only contacts whose <span className="mono">platform</span> attribute matches.</span>
              </div>
            </div>
          )}
          {channel === "voice" && (
            <div className="form mt">
              <label className="field">AI voice<select value={o.voice} onChange={(e) => setO({ ...o, voice: e.target.value })}>{VOICES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            </div>
          )}

          <div className="row mt" style={{ gap: 4 }}>
            <span className="muted" style={{ fontSize: 12 }}>Insert:</span>
            {TOKENS.map(([k, l]) => <button key={k} className="sm ghost" title={l} onClick={() => insertToken(k)}>{`{${k}}`}</button>)}
          </div>
          <textarea ref={bodyRef} className="mt" style={{ minHeight: 200, fontFamily: "inherit", fontSize: 14 }} placeholder={channel === "voice" ? "Hi {first_name}, this is VOYQI…" : "Write your message"} value={body} onChange={(e) => setBody(e.target.value)} />
          {channel === "sms" ? (
            <div className="row between">
              <label className="row" style={{ fontSize: 12 }}><input type="checkbox" checked={o.append_stop} onChange={(e) => setO({ ...o, append_stop: e.target.checked })} /> Add “{STOP_LINE}”</label>
              <span className={`counter ${smsOut.length > 320 ? "over" : ""}`}>{smsOut.length}/320 · {parts.parts} SMS part{parts.parts === 1 ? "" : "s"}{parts.unicode ? " · Unicode (70 per part)" : ""}</span>
            </div>
          ) : <div className={`counter ${limit && body.length > limit ? "over" : ""}`}>{body.length}{limit ? ` / ${limit}` : ""} characters</div>}
          <div className="muted" style={{ fontSize: 12 }}>Tokens are filled from each contact; one a contact has no value for is left blank.</div>
          <div className="row mt">
            <button disabled={!body} onClick={save}>{subjectB ? "Save A and B" : "Save draft"}</button>
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
            <div className="row between"><h3>Preview</h3><span className="muted" style={{ fontSize: 12 }}>as Ava Patel, Miami</span></div>
            <div className="mt">
              {channel === "email" ? (
                <div className="preview">
                  <div className="muted" style={{ fontSize: 12, color: "#555" }}>From: {o.from_name || "VOYQI"}{o.reply_to ? ` · reply to ${o.reply_to}` : ""}</div>
                  <div className="subj">{sample(subject) || "Subject line"}</div>
                  {o.preview_text && <div style={{ color: "#777", fontSize: 13, marginTop: -4, marginBottom: 8 }}>{sample(o.preview_text)}</div>}
                  {sample(body) || "Your message…"}
                </div>
              ) : channel === "push" ? (
                <div className="phone"><div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>🔔 VOYQI · now</div><div className="bubble"><b>{sample(subject) || "Title"}</b><div>{sample(body) || "Your message…"}</div></div>
                  <div className="muted" style={{ fontSize: 11, marginTop: 8 }}>Tap → {DEEP_LINKS.find(([k]) => k === o.deep_link)?.[1]}</div></div>
              ) : channel === "voice" ? (
                <div className="phone"><div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>📞 {VOICES.find(([k]) => k === o.voice)?.[1]}</div>
                  <div className="bubble"><span className="muted">This is an automated call from VOYQI. </span>{sample(body) || "Your script…"}</div></div>
              ) : (
                <div className="phone"><div className="muted" style={{ fontSize: 11, marginBottom: 8 }}>💬 {o.sender_id || "VOYQI"}</div>{o.media_url && <img src={o.media_url} alt="" style={{ width: "100%", borderRadius: 10, marginBottom: 6 }} />}<div className="bubble">{sample(smsOut) || "Your message…"}</div></div>
              )}
            </div>
          </div>
          {saved && (
            <>
              <DraftCard d={saved} onChange={async () => setSaved(await mk.drafts(saved.campaign_id).then((ds) => ds.find((d) => d.id === saved.id) || saved))} />
              {abNote && <div className="notice info">{abNote} <Link href="/marketing/ab-tests">A/B tests</Link></div>}
              {saved.status === "approved" && (
                <div className="card">
                  <h3>Schedule</h3>
                  <div className="row mt"><input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /><select value={recurrence} onChange={(e) => setRecurrence(e.target.value)}>{RECURRENCE.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select><button onClick={schedule}>Schedule send</button></div>
                </div>
              )}
              {saved.status === "scheduled" && <div className="notice ok">Scheduled. Send it to recipients from the <Link href={`/marketing/campaigns/${saved.campaign_id}`}>campaign page</Link>.</div>}
            </>
          )}
        </div>
      </div>
    </>
  );
}
