"use client";

import { Fragment, useState } from "react";
import { Badge, Empty, Loading, StatusBadge, fmtTime, useAction, useLoad } from "@/lib/ui";
import { Explain, SeoPage, seo, type Site } from "./seo";

const KINDS: [string, string][] = [["blog", "Article"], ["landing", "Landing page"], ["faq", "FAQ"], ["pillar", "Pillar page"]];
const words = (s: string) => (s.trim() ? s.trim().split(/\s+/).length : 0);
const toLocalInput = (iso: string) => { const d = new Date(iso); return new Date(d.getTime() - d.getTimezoneOffset() * 60e3).toISOString().slice(0, 16); };

export function Content() {
  return <SeoPage title="Content & programmatic" text="Briefs, articles and refreshes — each scored against the brief and checked by the brand guardrail before it can publish — and template pages behind the thin-content gate.">{(site) => <Body site={site} />}</SeoPage>;
}

function Body({ site }: { site: Site }) {
  const [tab, setTab] = useState("content");
  return (
    <>
      <Explain title="Two layers decide what happens to a piece.">The SEO score (minimum 70) sends weak drafts back for revision. The guardrail holds anything with absolute claims or banned phrases for a person — no agent can clear it, in any mode.</Explain>
      <div className="tabs">{[["content", "Content"], ["brief", "Build a brief"], ["refresh", "Pages worth refreshing"], ["pseo", "Programmatic pages"]].map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      {tab === "content" && <Pipeline site={site} />}
      {tab === "brief" && <Brief site={site} />}
      {tab === "refresh" && <Refresh site={site} />}
      {tab === "pseo" && <Programmatic site={site} />}
    </>
  );
}

function Pipeline({ site }: { site: Site }) {
  const run = useAction();
  const [status, setStatus] = useState("");
  const list = useLoad(() => seo.get<any[]>(`/sites/${site.id}/content${status ? `?status=${status}` : ""}`), [site.id, status]);
  const sched = useLoad(() => seo.get(`/sites/${site.id}/content/schedule`), [site.id]);
  const [f, setF] = useState({ target_keyword: "", title: "", kind: "blog" });
  const [open, setOpen] = useState<string | null>(null);
  const reloadAll = () => { list.reload(); sched.reload(); };
  const s = sched.data;
  return (
    <>
      <div className="split mb">
        <div className="card">
          <h3>Plan a piece</h3>
          <div className="form mt">
            <label className="field">Target keyword<input value={f.target_keyword} placeholder="car subscription cost" onChange={(e) => setF({ ...f, target_keyword: e.target.value })} /></label>
            <label className="field">Working title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
            <label className="field">Kind<select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          </div>
          <button className="mt" disabled={!f.target_keyword} onClick={() => run("Planned — the content agent writes it", async () => { const c = await seo.post(`/sites/${site.id}/content`, f); setOpen(c.id); }).then(() => { setF({ target_keyword: "", title: "", kind: "blog" }); reloadAll(); })}>Plan piece</button>
        </div>
        <div className="card">
          <h3>Publishing cadence</h3>
          {!s ? <Loading /> : (
            <>
              <div className="kv mt">
                <span>This week</span><span>{s.published_last_7d} of {s.weekly_target} published · {s.remaining_this_week} to go</span>
                <span>Pipeline</span><span>{Object.entries(s.by_status).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(" · ") || "empty"}</span>
                <span>Next slots</span><span>{s.recommended_slots.map((d: string) => new Date(d).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })).join(" · ")}</span>
              </div>
              {s.upcoming.length > 0 && <table className="mt"><tbody>{s.upcoming.map((u: any) => <tr key={u.id}><td>{u.title}</td><td className="muted">{fmtTime(u.publish_at)}</td></tr>)}</tbody></table>}
            </>
          )}
        </div>
      </div>
      <div className="card">
        <div className="row between"><h3>Pipeline</h3>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>{["", "planned", "needs_revision", "held_for_review", "approved", "scheduled", "published", "failed"].map((x) => <option key={x} value={x}>{x ? x.replace(/_/g, " ") : "all"}</option>)}</select>
        </div>
        {list.loading ? <Loading /> : !(list.data || []).length ? <div className="empty">Nothing here.</div> : (
          <table className="mt">
            <thead><tr><th>Piece</th><th>Kind</th><th>SEO score</th><th>Status</th><th>When</th><th /></tr></thead>
            <tbody>{(list.data || []).map((c) => (
              <Fragment key={c.id}>
                <tr>
                  <td><b>{c.title || c.target_keyword}</b><div className="muted" style={{ fontSize: 12 }}>{c.target_keyword}{c.refresh_of_url ? ` · refresh of ${c.refresh_of_url}` : ""}</div></td>
                  <td>{c.kind}</td>
                  <td>{c.seo_score ? <Badge kind={c.seo_score >= 70 ? "ok" : "warn"}>{c.seo_score}</Badge> : "—"}</td>
                  <td><StatusBadge value={c.status} />{c.guardrail_findings.length > 0 && <Badge kind="bad">guardrail</Badge>}{c.last_error && <div style={{ color: "var(--bad)", fontSize: 12 }}>{c.last_error}</div>}</td>
                  <td className="muted" style={{ fontSize: 12 }}>{c.published_at ? <>published {fmtTime(c.published_at)}{c.published_url && <div><a href={c.published_url} target="_blank" rel="noreferrer">view</a></div>}</> : c.publish_at ? `scheduled ${fmtTime(c.publish_at)}` : fmtTime(c.created_at)}</td>
                  <td><button className="sm ghost" onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? "Close" : "Open"}</button></td>
                </tr>
                {open === c.id && <tr><td colSpan={6}><Editor id={c.id} onChange={reloadAll} /></td></tr>}
              </Fragment>
            ))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}

function Editor({ id, onChange }: { id: string; onChange: () => void }) {
  const run = useAction();
  const c = useLoad(() => seo.get(`/content/${id}`), [id]);
  const [d, setD] = useState<any>(null);
  const [when, setWhen] = useState("");
  if (c.loading && !c.data) return <Loading />;
  const item = c.data;
  if (!item) return null;
  const draft = d || { title: item.title, meta_description: item.meta_description, body_markdown: item.body_markdown };
  const done = (label: string, fn: () => Promise<any>) => run(label, fn).then(() => { setD(null); c.reload(); onChange(); });
  return (
    <div className="card" style={{ background: "var(--panel-2)" }}>
      {(item.quality_findings.length > 0 || item.guardrail_findings.length > 0) && (
        <div className="split mb">
          {item.quality_findings.length > 0 && <div><h4>Quality ({item.seo_score}/100)</h4><ul className="findings">{item.quality_findings.map((q: any) => <li key={q.code}>{q.detail}</li>)}</ul></div>}
          {item.guardrail_findings.length > 0 && <div><h4>Held by the guardrail</h4><ul className="findings" style={{ color: "var(--bad)" }}>{item.guardrail_findings.map((g: any, i: number) => <li key={i}>{typeof g === "string" ? g : g.detail || JSON.stringify(g)}</li>)}</ul></div>}
        </div>
      )}
      <div className="form">
        <label className="field">Title<input value={draft.title} onChange={(e) => setD({ ...draft, title: e.target.value })} /><span className={draft.title.length > 60 ? "counter over" : "counter"}>{draft.title.length}/60</span></label>
        <label className="field" style={{ gridColumn: "span 2" }}>Meta description<input value={draft.meta_description} onChange={(e) => setD({ ...draft, meta_description: e.target.value })} /><span className={draft.meta_description.length > 160 ? "counter over" : "counter"}>{draft.meta_description.length}/160</span></label>
      </div>
      <textarea className="mt" style={{ minHeight: 260 }} placeholder={"# Heading\n\nWrite in Markdown…"} value={draft.body_markdown} onChange={(e) => setD({ ...draft, body_markdown: e.target.value })} />
      <div className="counter">{words(draft.body_markdown)} words · revision {item.revision}</div>
      <div className="row mt">
        <button disabled={!draft.title || !draft.body_markdown} onClick={() => done("Draft scored", () => seo.post(`/content/${id}/draft`, draft))}>Submit draft for scoring</button>
        {["held_for_review", "needs_revision"].includes(item.status) && <button className="ok" onClick={() => confirm(item.guardrail_findings.length ? "Approve despite the guardrail findings? You are the reviewer." : "Approve below the minimum score?") && done("Approved", () => seo.post(`/content/${id}/approve`))}>Approve as reviewer</button>}
        {["approved", "scheduled"].includes(item.status) && <>
          <input type="datetime-local" value={when || (item.publish_at ? toLocalInput(item.publish_at) : "")} onChange={(e) => setWhen(e.target.value)} />
          <button className="ghost" disabled={!when} onClick={() => done("Scheduled", () => seo.post(`/content/${id}/schedule`, { publish_at: new Date(when).toISOString() }))}>Schedule</button>
        </>}
        {["approved", "scheduled", "failed"].includes(item.status) && <button onClick={() => done("Published", () => seo.post(`/content/${id}/publish`))}>Publish now</button>}
      </div>
    </div>
  );
}

function Brief({ site }: { site: Site }) {
  const run = useAction();
  const [kw, setKw] = useState("");
  const [kind, setKind] = useState("blog");
  const [b, setB] = useState<any>(null);
  return (
    <div className="card">
      <h3>Build a content brief</h3>
      <div className="sub">From search intent, the questions people ask and what already ranks: format, angle, outline and what must be covered.</div>
      <div className="row mt">
        <input style={{ flex: 1 }} placeholder="Target keyword" value={kw} onChange={(e) => setKw(e.target.value)} />
        <select value={kind} onChange={(e) => setKind(e.target.value)}>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <button disabled={!kw} onClick={() => run("Brief ready", async () => setB(await seo.post(`/sites/${site.id}/content/brief`, { keyword: kw, kind })))}>Build brief</button>
      </div>
      {b && (
        <div className="split mt">
          <div>
            <h4>Outline</h4>
            <div className="pre mono" style={{ fontSize: 12.5 }}>{b.outline.join("\n")}</div>
            <div className="row mt">
              <button className="sm" onClick={() => run("Planned with this brief", () => seo.post(`/sites/${site.id}/content`, { target_keyword: b.keyword, kind: b.kind, title: b.outline[0]?.replace(/^#\s*/, "") || b.keyword, brief: b }))}>Plan a piece from this brief</button>
            </div>
          </div>
          <div className="kv">
            <span>Intent</span><span>{b.intent} · buying intent {b.purchase_intent}</span>
            <span>Format</span><span>{b.format} — {b.angle}</span>
            <span>Length</span><span>~{b.target_words} words</span>
            <span>Must include</span><span>{b.must_include.join(" · ")}</span>
            {b.secondary_keywords.length > 0 && <><span>Also target</span><span>{b.secondary_keywords.join(", ")}</span></>}
            {b.questions_to_answer.length > 0 && <><span>Answer</span><span>{b.questions_to_answer.join(" · ")}</span></>}
            <span>Never say</span><span className="muted">{b.banned_phrases.join(", ")}</span>
            {b.require_disclosure && <><span>Disclosure</span><span>{b.require_disclosure}</span></>}
          </div>
        </div>
      )}
    </div>
  );
}

function Refresh({ site }: { site: Site }) {
  const run = useAction();
  const { data, loading } = useLoad(() => seo.get(`/sites/${site.id}/content/refresh-candidates`), [site.id]);
  if (loading) return <Loading />;
  if (!data?.has_search_console_data) return <Empty>{data?.note || "Connect Search Console to find pages worth refreshing."}</Empty>;
  return (
    <div className="card table-wrap">
      <table>
        <thead><tr><th>Page</th><th>Keyword</th><th>Score</th><th>Why</th><th /></tr></thead>
        <tbody>{data.candidates.map((c: any) => (
          <tr key={c.url}><td><a href={c.url} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 12 }}>{c.url}</a></td><td>{c.keyword}</td><td><b>{c.score}</b></td>
            <td style={{ fontSize: 12 }}>{c.reasons.map((r: any, i: number) => <div key={i}>{typeof r === "string" ? r : r.detail || r.reason || JSON.stringify(r)}</div>)}</td>
            <td><button className="sm ghost" onClick={() => run("Refresh planned", () => seo.post(`/sites/${site.id}/content`, { target_keyword: c.keyword, refresh_of_url: c.url, kind: "refresh" }))}>Plan refresh</button></td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function Programmatic({ site }: { site: Site }) {
  const run = useAction();
  const tpls = useLoad(() => seo.get<any[]>(`/sites/${site.id}/programmatic/templates`), [site.id]);
  const [status, setStatus] = useState("");
  const pages = useLoad(() => seo.get<any[]>(`/sites/${site.id}/programmatic/pages${status ? `?status=${status}` : ""}`), [site.id, status]);
  const [t, setT] = useState({ name: "", slug_pattern: "car-subscription-{city}", title_pattern: "Car subscription in {city}", meta_pattern: "", body_template: "", required_slots: "city", min_words: 300 });
  const [rows, setRows] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<any>(null);
  const reloadAll = () => { tpls.reload(); pages.reload(); };
  const parseRows = (text: string) => {
    const [head, ...lines] = text.trim().split("\n");
    const cols = (head || "").split(",").map((c) => c.trim());
    return lines.filter((l) => l.trim()).map((l) => Object.fromEntries(l.split(",").map((v, i) => [cols[i], v.trim()])));
  };
  return (
    <>
      <Explain title="The thin-content gate.">A generated page is held unless it reaches the template&apos;s minimum words and is different enough from every other page. Held pages publish only after a person approves them.</Explain>
      <div className="card mb">
        <h3>New template</h3>
        <div className="form mt">
          <label className="field">Name<input value={t.name} placeholder="City pages" onChange={(e) => setT({ ...t, name: e.target.value })} /></label>
          <label className="field">Slug pattern<input className="mono" value={t.slug_pattern} onChange={(e) => setT({ ...t, slug_pattern: e.target.value })} /></label>
          <label className="field">Title pattern<input value={t.title_pattern} onChange={(e) => setT({ ...t, title_pattern: e.target.value })} /></label>
          <label className="field">Meta pattern<input value={t.meta_pattern} onChange={(e) => setT({ ...t, meta_pattern: e.target.value })} /></label>
          <label className="field">Required slots<input className="mono" value={t.required_slots} placeholder="city, hub_count, intro" onChange={(e) => setT({ ...t, required_slots: e.target.value })} /></label>
          <label className="field">Minimum words<input type="number" value={t.min_words} onChange={(e) => setT({ ...t, min_words: Number(e.target.value) })} /></label>
        </div>
        <textarea className="mt" style={{ minHeight: 110, fontFamily: "inherit" }} placeholder="Body with {slots}: VOYQI has {hub_count} hubs in {city}. {intro}…" value={t.body_template} onChange={(e) => setT({ ...t, body_template: e.target.value })} />
        <button className="mt" disabled={!t.name || !t.slug_pattern || !t.title_pattern || !t.body_template} onClick={() => run("Template created", () => seo.post(`/sites/${site.id}/programmatic/templates`, { ...t, required_slots: t.required_slots.split(/[\s,]+/).filter(Boolean) })).then(reloadAll)}>Create template</button>
      </div>
      {(tpls.data || []).map((tp) => (
        <div key={tp.id} className="card mb">
          <div className="row between"><div><b>{tp.name}</b> <span className="mono muted">{tp.slug_pattern}</span></div><div className="row"><Badge>{tp.pending_rows} rows waiting</Badge><Badge>min {tp.min_words} words</Badge></div></div>
          <div className="sub mt">Rows as CSV with a header — columns: <span className="mono">{tp.required_slots.join(", ")}</span></div>
          <textarea className="mt" style={{ minHeight: 70 }} placeholder={`${tp.required_slots.join(",")}\n${tp.required_slots.map(() => "…").join(",")}`} value={rows[tp.id] || ""} onChange={(e) => setRows({ ...rows, [tp.id]: e.target.value })} />
          <div className="row mt">
            <button className="sm ghost" disabled={!rows[tp.id]?.includes("\n")} onClick={() => run("Previewed", async () => setPreview(await seo.post(`/programmatic/templates/${tp.id}/preview`, { row: parseRows(rows[tp.id])[0] || {} })))}>Preview first row</button>
            <button className="sm" disabled={!rows[tp.id]?.includes("\n")} onClick={() => run("Rows added", () => seo.post(`/programmatic/templates/${tp.id}/rows`, { rows: parseRows(rows[tp.id]) })).then(() => { setRows({ ...rows, [tp.id]: "" }); reloadAll(); })}>Add rows</button>
            <button className="sm ghost" onClick={() => run("Published", async () => { const r = await seo.post(`/programmatic/templates/${tp.id}/publish-batch`, { limit: 25 }); return r; }).then(reloadAll)}>Publish approved (25)</button>
          </div>
          {preview && <div className="card mt" style={{ background: "var(--panel-2)" }}><b>{preview.title}</b> <span className="mono muted">/{preview.slug}</span><div className="muted">{preview.meta_description}</div><div className="pre mt" style={{ maxHeight: 160, overflow: "auto" }}>{preview.body}</div><div className={preview.words < tp.min_words ? "counter over" : "counter"}>{preview.words} words</div></div>}
        </div>
      ))}
      <div className="card">
        <div className="row between">
          <h3>Generated pages</h3>
          <div className="row">
            <select value={status} onChange={(e) => setStatus(e.target.value)}>{["", "held", "approved", "published", "failed"].map((x) => <option key={x} value={x}>{x || "all"}</option>)}</select>
            <button className="sm" onClick={() => run("Generated", async () => { const r = await seo.post(`/sites/${site.id}/programmatic/generate`); return r; }).then(reloadAll)}>Generate pages</button>
          </div>
        </div>
        {pages.loading ? <Loading /> : !(pages.data || []).length ? <div className="empty">No pages yet — add rows, then generate.</div> : (
          <table className="mt"><thead><tr><th>Page</th><th>Words</th><th>Unique</th><th>Status</th><th /></tr></thead>
            <tbody>{(pages.data || []).map((p) => (
              <tr key={p.id}><td><b>{p.title}</b><div className="mono muted" style={{ fontSize: 12 }}>/{p.slug}</div></td><td>{p.words}</td><td>{Math.round(p.unique_ratio * 100)}%</td>
                <td><StatusBadge value={p.status} />{p.hold_reasons.map((r: string) => <div key={r} className="muted" style={{ fontSize: 12 }}>{r}</div>)}{p.published_url && <div><a href={p.published_url} target="_blank" rel="noreferrer">view</a></div>}</td>
                <td>{p.status === "held" && <button className="sm ok" onClick={() => run("Approved", () => seo.post(`/programmatic/pages/${p.id}/approve`)).then(pages.reload)}>Approve</button>}</td></tr>
            ))}</tbody></table>
        )}
      </div>
    </>
  );
}
