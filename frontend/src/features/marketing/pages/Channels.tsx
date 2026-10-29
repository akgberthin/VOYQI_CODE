"use client";

import { useState } from "react";
import Link from "next/link";
import { useSession } from "@/lib/session";
import { Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "@/lib/ui";
import { Ch, DraftCard } from "../bits";
import { CHANNELS, CHANNEL_LABEL, CLASSES, OBJECTIVES, RECURRENCE, TONES, fileToBase64, inHours, mk, toIso } from "../mk";

const PLATFORMS = ["instagram", "facebook", "linkedin", "twitter", "tiktok"];

export function Social() {
  const run = useAction();
  const accounts = useLoad(() => mk.accounts());
  const posts = useLoad(() => mk.posts());
  const drafts = useLoad(async () => (await mk.drafts(undefined, "approved")).filter((d) => d.channel === "social"));
  const assets = useLoad(() => mk.assets());
  const [acct, setAcct] = useState({ platform: "instagram", handle: "" });
  const [post, setPost] = useState({ social_account_id: "", content_draft_id: "", media_asset_id: "", at: inHours(4), recurrence: "none" });
  const reloadAll = () => { accounts.reload(); posts.reload(); drafts.reload(); };
  const handle = (id: string) => (accounts.data || []).find((a) => a.id === id);
  return (
    <>
      <PageHead title="◎ Social" text="Connected accounts and scheduled posts. Posts publish automatically at their time through each platform's API (set up under super admin → Integrations: Meta for Facebook and Instagram, LinkedIn, X); without an integration they're recorded in console mode, not posted." />
      <div className="split mb">
        <div className="card">
          <h3>Accounts</h3>
          <div className="row mt">
            <select value={acct.platform} onChange={(e) => setAcct({ ...acct, platform: e.target.value })}>{PLATFORMS.map((p) => <option key={p}>{p}</option>)}</select>
            <input placeholder="@voyqi" value={acct.handle} onChange={(e) => setAcct({ ...acct, handle: e.target.value })} />
            <button disabled={!acct.handle} onClick={() => run("Account connected", () => mk.connectAccount(acct)).then(() => { setAcct({ ...acct, handle: "" }); accounts.reload(); })}>Connect</button>
          </div>
          <table className="mt"><tbody>{(accounts.data || []).map((a) => (
            <tr key={a.id}><td><b>{a.platform}</b></td><td>{a.handle}</td><td>{a.connected ? <span className="badge ok">connected</span> : <span className="badge">disconnected</span>}</td>
              <td>{a.connected && <button className="sm ghost" onClick={() => run("Disconnected", () => mk.disconnectAccount(a.id)).then(accounts.reload)}>Disconnect</button>}</td></tr>
          ))}</tbody></table>
          {!accounts.loading && !(accounts.data || []).length && <Empty>No accounts connected.</Empty>}
        </div>
        <div className="card">
          <h3>Schedule a post</h3>
          {!(drafts.data || []).length ? <div className="sub mt">No approved social content. <Link href="/marketing/campaigns">Create a social campaign</Link> and draft a post.</div> : (
            <div className="form mt">
              <label className="field">Account<select value={post.social_account_id} onChange={(e) => setPost({ ...post, social_account_id: e.target.value })}><option value="">— choose —</option>{(accounts.data || []).filter((a) => a.connected).map((a) => <option key={a.id} value={a.id}>{a.platform} {a.handle}</option>)}</select></label>
              <label className="field">Content<select value={post.content_draft_id} onChange={(e) => setPost({ ...post, content_draft_id: e.target.value })}><option value="">— choose —</option>{(drafts.data || []).map((d) => <option key={d.id} value={d.id}>{d.body_text.slice(0, 50)}</option>)}</select></label>
              <label className="field">Image<select value={post.media_asset_id} onChange={(e) => setPost({ ...post, media_asset_id: e.target.value })}><option value="">none</option>{(assets.data || []).map((a) => <option key={a.id} value={a.id}>{a.title.slice(0, 50) || a.id.slice(0, 8)} ({a.source})</option>)}</select></label>
              <label className="field">When<input type="datetime-local" value={post.at} onChange={(e) => setPost({ ...post, at: e.target.value })} /></label>
              <label className="field">Repeat<select value={post.recurrence} onChange={(e) => setPost({ ...post, recurrence: e.target.value })}>{RECURRENCE.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            </div>
          )}
          {post.media_asset_id && <img src={(assets.data || []).find((a) => a.id === post.media_asset_id)?.url} alt="" style={{ maxWidth: 220, borderRadius: 8, marginTop: 10 }} />}
          {(accounts.data || []).find((a) => a.id === post.social_account_id)?.platform === "instagram" && !post.media_asset_id && <div className="notice warn mt">Instagram only publishes posts with an image.</div>}
          <button className="mt" disabled={!post.social_account_id || !post.content_draft_id} onClick={() => run("Post scheduled — it publishes automatically at that time", () => mk.schedulePost({ social_account_id: post.social_account_id, content_draft_id: post.content_draft_id, media_asset_id: post.media_asset_id || null, scheduled_at: toIso(post.at), recurrence: post.recurrence })).then(reloadAll)}>Schedule post</button>
        </div>
      </div>
      <h3>Posts</h3>
      {posts.loading ? <Loading /> : !(posts.data || []).length ? <Empty>No posts yet.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Account</th><th>Scheduled</th><th>Status</th><th>Platform post</th><th /></tr></thead>
          <tbody>{(posts.data || []).map((p) => {
            const a = handle(p.social_account_id);
            return (
              <tr key={p.id}><td>{a ? `${a.platform} ${a.handle}` : "—"}</td><td>{fmtTime(p.scheduled_at)}</td><td><StatusBadge value={p.status} /></td><td className="mono muted">{p.delivery_mode === "console" ? <span title="No integration for this platform — recorded, not posted">console only</span> : p.platform_post_id || "—"}{p.published_at && <div>{fmtTime(p.published_at)}</div>}{p.error_detail && <div style={{ color: "var(--bad)", maxWidth: 320, whiteSpace: "normal" }}>{p.error_detail}</div>}</td>
                <td>{p.status === "scheduled" && <button className="sm" onClick={() => run("Published", () => mk.publishPost(p.id)).then(posts.reload)}>Publish now</button>}</td></tr>
            );
          })}</tbody>
        </table></div>
      )}
    </>
  );
}

export function Review() {
  const [status, setStatus] = useState("held_for_review");
  const { data, error, loading, reload } = useLoad(() => mk.drafts(undefined, status || undefined), [status]);
  const campaigns = useLoad(() => mk.campaigns());
  const name = (id: string) => (campaigns.data || []).find((c) => c.id === id)?.name || "";
  return (
    <>
      <PageHead title="Content review" text="Every draft — AI-written or not — passes a deterministic brand and compliance guardrail. Passing drafts are approved automatically; the rest wait here for a person.">
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {["held_for_review", "approved", "scheduled", "sent", "rejected", ""].map((s) => <option key={s} value={s}>{s ? s.replace(/_/g, " ") : "all"}</option>)}
        </select>
      </PageHead>
      <ErrorNote error={error} />
      {loading ? <Loading /> : !(data || []).length ? <Empty>{status === "held_for_review" ? "Nothing held — every draft passed the guardrail." : "No drafts."}</Empty> : (
        <div className="grid cols-2">{(data || []).map((d) => (
          <div key={d.id}><div className="muted mb" style={{ fontSize: 12 }}><Link href={`/marketing/campaigns/${d.campaign_id}`}>{name(d.campaign_id)}</Link></div><DraftCard d={d} onChange={reload} /></div>
        ))}</div>
      )}
    </>
  );
}

export function Studio() {
  const run = useAction();
  const { me } = useSession();
  const media = useLoad(() => mk.media());
  const assets = useLoad(() => mk.assets());
  const [f, setF] = useState({ prompt: "", media_type: "image" });
  const pending = (media.data || []).some((m) => m.status === "generating");
  const reloadAll = () => { media.reload(); assets.reload(); };
  const upload = async (file: File) => run("Image uploaded", async () => mk.uploadAsset({ title: file.name, content_type: file.type, data_base64: await fileToBase64(file), created_by: me?.email || "marketing" })).then(assets.reload);
  return (
    <>
      <PageHead title="✦ AI studio" text="Images for campaigns and social posts — generated from a prompt (OpenAI, set up under super admin → Integrations) or uploaded. They're hosted by VOYQI so social platforms can fetch them. Copy is drafted on each campaign and in the composer.">
        <label className="btn ghost" style={{ cursor: "pointer" }}>Upload image<input type="file" accept="image/png,image/jpeg,image/webp,image/gif" style={{ display: "none" }} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>
        <button className="ghost" onClick={reloadAll}>Refresh</button>
      </PageHead>
      <Generator />
      <div className="card mb">
        <h3>Generate an image</h3>
        <div className="row mt">
          <select value={f.media_type} onChange={(e) => setF({ ...f, media_type: e.target.value })}><option value="image">image</option><option value="video">video</option></select>
          <input style={{ flex: 1 }} placeholder="A silver Model Y at golden hour on a Texas highway, cinematic" value={f.prompt} onChange={(e) => setF({ ...f, prompt: e.target.value })} />
          <button disabled={!f.prompt} onClick={() => run("Requested — generating", () => mk.requestMedia({ ...f, requested_by: me?.email || "marketing" })).then(() => { setF({ ...f, prompt: "" }); reloadAll(); })}>Generate</button>
        </div>
        {pending && <div className="notice info mt"><span className="spin" /> Generating… refresh in a minute.</div>}
      </div>
      <h3>Image library</h3>
      {assets.loading ? <Loading /> : !(assets.data || []).length ? <Empty>No images yet — generate or upload one.</Empty> : (
        <div className="grid cols-4 mb" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}>{(assets.data || []).map((a) => (
          <div key={a.id} className="card" style={{ padding: 10 }}>
            <img src={a.url} alt="" style={{ width: "100%", aspectRatio: "1", objectFit: "cover", borderRadius: 8, background: "var(--panel-2)" }} />
            <div className="row between mt"><span className="clip" title={a.title} style={{ maxWidth: 140 }}>{a.title || "untitled"}</span><span className="badge">{a.source}</span></div>
          </div>
        ))}</div>
      )}
      <h3>Requests</h3>
      {media.loading ? <Loading /> : !(media.data || []).length ? <Empty>No generation requests.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Prompt</th><th>Type</th><th>Status</th><th>Result</th></tr></thead>
          <tbody>{(media.data || []).map((m) => (
            <tr key={m.id}><td style={{ maxWidth: 420 }}>{m.prompt}</td><td>{m.media_type}</td><td><StatusBadge value={m.status} /></td>
              <td>{m.result_url ? <a href={m.result_url} target="_blank" rel="noreferrer">open</a> : m.error_detail ? <span style={{ color: "var(--bad)" }}>{m.error_detail}</span> : m.status === "pending" ? <span className="muted">waiting for an image provider (Integrations → OpenAI)</span> : "—"}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}

function Generator() {
  const run = useAction();
  const { me } = useSession();
  const campaigns = useLoad(() => mk.campaigns());
  const [f, setF] = useState({ channel: "email", mobility_class: "luxury", objective: "acquisition", context: "", tones: ["premium", "personal", "urgent"] });
  const [variants, setVariants] = useState<{ tone: string; source: string; subject: string; body: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<Record<number, string>>({});
  const generate = async () => {
    setBusy(true);
    await run("Variants ready", async () => setVariants((await mk.compose(f)).variants));
    setBusy(false);
  };
  const toggleTone = (t: string) => setF({ ...f, tones: f.tones.includes(t) ? f.tones.filter((x) => x !== t) : [...f.tones, t] });
  const options = (campaigns.data || []).filter((c) => c.channel === f.channel);
  return (
    <div className="card mb">
      <h3>✦ Generate campaign content</h3>
      <div className="sub">Copy variants from the Marketing Content Agent, one per tone — pick one, put it on a campaign or save it as a template.</div>
      <div className="form mt">
        <label className="field">Target class<select value={f.mobility_class} onChange={(e) => setF({ ...f, mobility_class: e.target.value })}>{CLASSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label className="field">Channel<select value={f.channel} onChange={(e) => { setF({ ...f, channel: e.target.value }); setVariants([]); }}>{["email", "sms", "push", "voice", "social"].map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></label>
        <label className="field">Objective<select value={f.objective} onChange={(e) => setF({ ...f, objective: e.target.value })}>{OBJECTIVES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label className="field" style={{ gridColumn: "1 / -1" }}>Additional context<input value={f.context} placeholder="e.g. Brickell hub opens 1 November" onChange={(e) => setF({ ...f, context: e.target.value })} /></label>
      </div>
      <div className="row mt">{TONES.map(([k, l]) => <label key={k} className="badge" style={{ cursor: "pointer" }}><input type="checkbox" checked={f.tones.includes(k)} onChange={() => toggleTone(k)} /> {l}</label>)}</div>
      <button className="mt" disabled={busy || !f.tones.length} onClick={generate}>{busy ? "Generating…" : variants.length ? "Regenerate" : "Generate"}</button>
      {variants.length > 0 && (
        <div className="grid cols-3 mt">{variants.map((v, i) => (
          <div key={v.tone + i} className="card" style={{ background: "var(--panel-2)" }}>
            <div className="row between"><b>{TONES.find(([k]) => k === v.tone)?.[1] || v.tone}</b><span className={`badge ${v.source === "claude" ? "ai" : ""}`}>{v.source === "claude" ? "Claude" : "template"}</span></div>
            {v.subject && <div className="mt"><b>{v.subject}</b></div>}
            <div className="mt" style={{ whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{v.body}</div>
            <div className="row mt">
              <select value={target[i] || ""} onChange={(e) => setTarget({ ...target, [i]: e.target.value })}><option value="">{options.length ? "campaign…" : `no ${f.channel} campaigns`}</option>{options.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
              <button className="sm" disabled={!target[i]} onClick={() => run("Added to the campaign — guardrail checked", () => mk.createDraft({ campaign_id: target[i], channel: f.channel as any, subject_line: v.subject, body_text: v.body, created_by: me?.email || "marketing" } as any))}>Use</button>
            </div>
            <div className="row mt">
              <button className="sm ghost" onClick={() => run("Saved to library", () => mk.saveToLibrary({ title: v.subject || `${v.tone} ${f.channel}`, channel: f.channel, body_text: v.subject ? `${v.subject}\n\n${v.body}` : v.body, tags: [v.tone, f.objective], created_by: me?.email || "marketing" }))}>Save to library</button>
              <button className="sm ghost" onClick={() => navigator.clipboard?.writeText(v.subject ? `${v.subject}\n\n${v.body}` : v.body)}>Copy</button>
            </div>
          </div>
        ))}</div>
      )}
    </div>
  );
}

export function Library() {
  const run = useAction();
  const { me } = useSession();
  const [channel, setChannel] = useState("");
  const [q, setQ] = useState("");
  const { data: all, loading, reload } = useLoad(() => mk.library(), []);
  const data = (all || []).filter((t) => (!channel || t.channel === channel) && (!q || `${t.title} ${t.body_text} ${t.tags.join(" ")}`.toLowerCase().includes(q.toLowerCase())));
  const [f, setF] = useState({ title: "", channel: "email", body_text: "", tags: "" });
  return (
    <>
      <PageHead title="Library" text="Reusable templates per channel. Load one in the composer to start a message from it.">
        <input placeholder="Search title, text or tag…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">all channels</option>{CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select>
      </PageHead>
      <div className="grid cols-4 mb" style={{ gridTemplateColumns: "repeat(5, minmax(0,1fr))" }}>{CHANNELS.map((c) => (
        <button key={c} className="card kpi" style={{ textAlign: "left", background: channel === c ? "var(--panel-2)" : undefined, borderColor: channel === c ? "var(--gold)" : undefined, color: "inherit" }} onClick={() => setChannel(channel === c ? "" : c)}>
          <div className="l"><Ch c={c} /></div><div className="n">{(all || []).filter((t) => t.channel === c).length}</div><div className="d">templates</div>
        </button>
      ))}</div>
      <div className="card mb">
        <div className="form">
          <label className="field">Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
          <label className="field">Channel<select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>{CHANNELS.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}</select></label>
          <label className="field">Tags<input placeholder="welcome, ev" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} /></label>
        </div>
        <textarea className="mt" style={{ fontFamily: "inherit" }} placeholder="For email, put the subject on the first line, then a blank line, then the body." value={f.body_text} onChange={(e) => setF({ ...f, body_text: e.target.value })} />
        <button className="mt" disabled={!f.title || !f.body_text} onClick={() => run("Template saved", () => mk.saveToLibrary({ ...f, tags: f.tags.split(",").map((t) => t.trim()).filter(Boolean), created_by: me?.email || "marketing" })).then(() => { setF({ ...f, title: "", body_text: "", tags: "" }); reload(); })}>Save template</button>
      </div>
      {loading ? <Loading /> : !data.length ? <Empty>{q || channel ? "Nothing matches." : "No templates."}</Empty> : (
        <div className="grid cols-3">{data.map((t) => (
          <div key={t.id} className="card">
            <div className="row between"><b>{t.title}</b><Ch c={t.channel} /></div>
            <div className="mt muted" style={{ whiteSpace: "pre-wrap", maxHeight: 140, overflow: "hidden" }}>{t.body_text}</div>
            <div className="row mt">{t.tags.map((x: string) => <span key={x} className="badge">{x}</span>)}</div>
            <div className="row mt"><Link href={`/marketing/compose/${t.channel === "social" ? "email" : t.channel}`}><button className="sm ghost">Use</button></Link><button className="sm ghost" onClick={() => run("Deleted", () => mk.deleteFromLibrary(t.id)).then(reload)}>Delete</button></div>
          </div>
        ))}</div>
      )}
    </>
  );
}

export function Leads() {
  const { data, loading, error } = useLoad(() => mk.leads());
  const campaigns = useLoad(() => mk.campaigns());
  const name = (id: string | null) => (id && (campaigns.data || []).find((c) => c.id === id)?.name) || "—";
  return (
    <>
      <PageHead title="Leads" text="People who filled a landing-page form. Each one is also turned into a scored CRM lead by the Revenue Agent." />
      <ErrorNote error={error} />
      {loading ? <Loading /> : !(data || []).length ? <Empty>No leads yet.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Name</th><th>Contact</th><th>Landing page</th><th>Campaign</th><th>Consent</th><th>When</th></tr></thead>
          <tbody>{(data || []).slice().reverse().map((l) => (
            <tr key={l.id}><td><b>{l.name}</b></td><td>{l.email}<div className="muted">{l.phone}</div></td><td className="mono">{l.landing_page}</td><td>{name(l.campaign_id)}</td><td>{l.consent_marketing ? <span className="badge ok">yes</span> : <span className="badge">no</span>}</td><td className="muted">{fmtTime(l.created_at)}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
