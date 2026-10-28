"use client";

import { Fragment, useState } from "react";
import { Badge, Empty, Loading, StatusBadge, fmtTime, pretty, useAction, useLoad } from "@/lib/ui";
import { Bars, Explain, Kpi, LineChart, SeoPage, fmtN, fmtPct, seo, sev, type Site } from "./seo";

// ── Technical SEO ─────────────────────────────────────────────────────
export function Technical() {
  return <SeoPage title="Technical SEO" text="Crawl audits with Core Web Vitals, the issues they find, and fixes — the low-risk ones applied automatically within the daily cap, the rest only with a person's approval.">{(site) => <TechBody site={site} />}</SeoPage>;
}

function TechBody({ site }: { site: Site }) {
  const run = useAction();
  const [f, setF] = useState({ status: "", severity: "" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString();
  const issues = useLoad(() => seo.get<any[]>(`/sites/${site.id}/technical/issues${qs ? `?${qs}` : ""}`), [site.id, qs]);
  const audits = useLoad(() => seo.get<any[]>(`/sites/${site.id}/technical/audits`), [site.id]);
  const policy = useLoad(() => seo.get("/technical/fix-policy"));
  const [busy, setBusy] = useState(false);
  const [fixing, setFixing] = useState<any>(null);
  const [value, setValue] = useState("");
  const reloadAll = () => { issues.reload(); audits.reload(); };
  const audit = async () => { setBusy(true); await run("Audit finished", () => seo.post(`/sites/${site.id}/technical/audits/run`)); setBusy(false); reloadAll(); };
  const fix = (i: any, body: any = {}) => run("Fix applied", async () => {
    const r = await seo.post(`/technical/issues/${i.id}/fix`, body);
    if (r.status === "failed" || r.fix_result?.applied === false) throw new Error(r.fix_result?.detail || "the fix could not be applied");
  }).then(() => { setFixing(null); setValue(""); reloadAll(); });
  const last = (audits.data || [])[0];
  const p = policy.data;
  return (
    <>
      <div className="row mb"><button disabled={busy} onClick={audit}>{busy ? "Crawling…" : "Run audit now"}</button>{last && <span className="muted">last audit {fmtTime(last.created_at)} · {last.pages_scanned} pages</span>}</div>
      <div className="grid cols-4 mb">
        <Kpi label="Health" value={last ? `${last.health_score}/100` : "—"} tone={last ? (last.health_score >= 80 ? "ok" : last.health_score >= 50 ? "warn" : "bad") : undefined} />
        <Kpi label="Critical" value={fmtN(last?.issue_counts?.critical ?? 0)} tone={last?.issue_counts?.critical ? "bad" : undefined} />
        <Kpi label="Warnings" value={fmtN(last?.issue_counts?.warning ?? 0)} />
        <Kpi label="Pages scanned" value={fmtN(last?.pages_scanned ?? 0)} />
      </div>
      {p && (
        <div className="split mb">
          <div className="card"><h3>May be fixed automatically</h3><div className="row mt">{p.auto_apply.map((x: string) => <Badge key={x} kind="ok">{pretty(x)}</Badge>)}</div>
            <h4 className="mt">Never automatically</h4><div className="row">{p.never_auto.map((x: string) => <Badge key={x} kind="bad">{pretty(x)}</Badge>)}</div></div>
          <div className="card"><h3>Core Web Vitals thresholds</h3>
            <table className="mt"><thead><tr><th>Metric</th><th>Good</th><th>Poor above</th></tr></thead><tbody>
              <tr><td>Largest Contentful Paint</td><td>≤ {p.core_web_vitals.lcp_ms[0]} ms</td><td>{p.core_web_vitals.lcp_ms[1]} ms</td></tr>
              <tr><td>Interaction to Next Paint</td><td>≤ {p.core_web_vitals.inp_ms[0]} ms</td><td>{p.core_web_vitals.inp_ms[1]} ms</td></tr>
              <tr><td>Cumulative Layout Shift</td><td>≤ {p.core_web_vitals.cls[0]}</td><td>{p.core_web_vitals.cls[1]}</td></tr>
            </tbody></table></div>
        </div>
      )}
      {fixing && (
        <div className="card mb" style={{ borderColor: "var(--gold)" }}>
          <h3>Fix: {pretty(fixing.fix_plan?.fix_type || fixing.code)}</h3>
          <div className="sub">{fixing.url} — {fixing.detail}</div>
          {fixing.fix_plan?.fix_type === "add_alt_text"
            ? <textarea className="mt" style={{ minHeight: 70 }} placeholder={'{"https://…/hero.jpg": "Silver EV at the Brickell hub"}'} value={value} onChange={(e) => setValue(e.target.value)} />
            : <input className="mt" style={{ width: "100%" }} placeholder="The new text" value={value} onChange={(e) => setValue(e.target.value)} />}
          <div className="row mt">
            <button disabled={!value} onClick={() => { let body: any; try { body = fixing.fix_plan?.fix_type === "add_alt_text" ? { alt_texts: JSON.parse(value) } : { value }; } catch (e: any) { return alert(`Invalid JSON: ${e.message}`); } fix(fixing, body); }}>Apply</button>
            <button className="ghost" onClick={() => setFixing(null)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="card mb">
        <div className="row between"><h3>Issues</h3>
          <div className="row">
            <select value={f.severity} onChange={(e) => setF({ ...f, severity: e.target.value })}><option value="">any severity</option>{["critical", "warning", "info"].map((s) => <option key={s}>{s}</option>)}</select>
            <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">open</option>{["awaiting_approval", "resolved", "failed", "dismissed"].map((s) => <option key={s} value={s}>{pretty(s)}</option>)}</select>
          </div>
        </div>
        {issues.loading ? <Loading /> : !(issues.data || []).length ? <div className="empty">{last ? "No issues here." : "Run an audit to find issues (needs the site crawler connected)."}</div> : (
          <div className="table-wrap"><table className="mt">
            <thead><tr><th>Issue</th><th>Page</th><th>Fix</th><th>Status</th><th /></tr></thead>
            <tbody>{(issues.data || []).map((i) => {
              const needsText = i.fix_plan?.params?.needs_generation;
              return (
                <tr key={i.id}>
                  <td><Badge kind={sev(i.severity)}>{i.severity}</Badge> <b>{pretty(i.code)}</b><div className="muted" style={{ fontSize: 12 }}>{i.detail}</div></td>
                  <td className="mono clip" style={{ fontSize: 12 }} title={i.url}>{i.url}</td>
                  <td style={{ fontSize: 12 }}>{i.auto_fixable ? <>{pretty(i.fix_plan?.fix_type || "")} <Badge kind={i.risk === "low" ? "ok" : "warn"}>{i.risk} risk</Badge></> : <span className="muted">needs a person</span>}</td>
                  <td><StatusBadge value={i.status} />{i.fix_result?.detail && <div className="muted" style={{ fontSize: 12 }}>{i.fix_result.detail}</div>}</td>
                  <td><div className="row">
                    {i.status === "awaiting_approval" && <button className="sm ok" onClick={() => run("Approved — it can be applied now", () => seo.post(`/technical/issues/${i.id}/approve`)).then(reloadAll)}>Approve</button>}
                    {i.auto_fixable && ["open", "approved", "failed"].includes(i.status) && <button className="sm" onClick={() => (needsText ? setFixing(i) : fix(i))}>Fix</button>}
                    {!["resolved", "dismissed"].includes(i.status) && <button className="sm ghost" onClick={() => run("Dismissed", () => seo.post(`/technical/issues/${i.id}/dismiss`)).then(reloadAll)}>Dismiss</button>}
                  </div></td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </div>
      <div className="split">
        <SchemaBuilder />
        <RedirectResolver />
      </div>
      {(audits.data || []).length > 1 && (
        <div className="card mt"><h3>Audit history</h3>
          <table className="mt"><tbody>{(audits.data || []).map((a) => <tr key={a.id}><td className="muted">{fmtTime(a.created_at)}</td><td>{a.pages_scanned} pages</td><td>health {a.health_score}</td><td className="muted">{Object.entries(a.issue_counts || {}).map(([k, v]) => `${v} ${k}`).join(" · ")}</td></tr>)}</tbody></table>
        </div>
      )}
    </>
  );
}

const SCHEMA_TYPES = ["Organization", "LocalBusiness", "AutoRental", "Product", "Service", "FAQPage", "Article", "BreadcrumbList", "WebSite"];

function SchemaBuilder() {
  const run = useAction();
  const [type, setType] = useState("Organization");
  const [entity, setEntity] = useState('{\n  "name": "VOYQI",\n  "url": "https://voyqi.com"\n}');
  const [out, setOut] = useState<any>(null);
  const [check, setCheck] = useState<any>(null);
  const build = () => run("Built", async () => {
    let e;
    try { e = JSON.parse(entity); } catch (err: any) { throw new Error(`Entity isn't valid JSON: ${err.message}`); }
    const jsonld = await seo.post("/technical/schema/build", { schema_type: type, entity: e });
    setOut(jsonld);
    setCheck(await seo.post("/technical/schema/validate", { jsonld }));
  });
  return (
    <div className="card">
      <h3>Structured data (JSON-LD)</h3>
      <div className="row mt"><select value={type} onChange={(e) => setType(e.target.value)}>{SCHEMA_TYPES.map((t) => <option key={t}>{t}</option>)}</select><button onClick={build}>Build & validate</button></div>
      <textarea className="mt" style={{ minHeight: 90 }} value={entity} onChange={(e) => setEntity(e.target.value)} />
      {out && <pre className="json mt">{JSON.stringify(out, null, 2)}</pre>}
      {check && <div className={`notice ${check.valid ? "ok" : "bad"} mt`}>{check.valid ? "Valid." : check.problems.join(" · ")}</div>}
    </div>
  );
}

function RedirectResolver() {
  const run = useAction();
  const [text, setText] = useState("/old-plans -> /plans\n/plans -> /membership");
  const [out, setOut] = useState<any>(null);
  const resolve = () => run("Resolved", async () => {
    const rules = text.split("\n").map((l) => l.split(/\s*(?:->|→|,)\s*/)).filter((p) => p[0] && p[1]).map(([from, to]) => ({ from: from.trim(), to: to.trim() }));
    setOut(await seo.post("/technical/redirects/resolve", { rules }));
  });
  return (
    <div className="card">
      <h3>Redirect chain resolver</h3>
      <div className="sub">One rule per line: <span className="mono">/from -&gt; /to</span>. Chains are flattened to one hop; loops are flagged.</div>
      <textarea className="mt" style={{ minHeight: 90 }} value={text} onChange={(e) => setText(e.target.value)} />
      <button className="mt" onClick={resolve}>Resolve</button>
      {out && (
        <>
          {out.loops.length > 0 && <div className="notice bad mt"><b>Redirect loop.</b> {out.loops.map((l: any) => (Array.isArray(l) ? l.join(" → ") : JSON.stringify(l))).join("; ")}</div>}
          <table className="mt"><thead><tr><th>From</th><th>To</th><th>Hops before</th></tr></thead><tbody>{out.resolved.map((r: any) => <tr key={r.from}><td className="mono">{r.from}</td><td className="mono">{r.to}</td><td>{r.hops}{r.hops > 1 && <Badge kind="warn">flattened</Badge>}</td></tr>)}</tbody></table>
        </>
      )}
    </div>
  );
}

// ── AI search (GEO) ───────────────────────────────────────────────────
const ENGINES: [string, string][] = [["chatgpt", "ChatGPT"], ["perplexity", "Perplexity"], ["claude", "Claude"], ["google_ai", "Google AI"]];

export function Geo() {
  return <SeoPage title="AI search (GEO)" text="Whether ChatGPT, Perplexity, Claude and Google's AI answers mention and cite the brand for the prompts that matter, who they name instead, and which sources they lean on.">{(site) => <GeoBody site={site} />}</SeoPage>;
}

function GeoBody({ site }: { site: Site }) {
  const run = useAction();
  const d = useLoad(async () => {
    const [vis, prompts, checks, gaps] = await Promise.all([
      seo.get(`/sites/${site.id}/geo/visibility`), seo.get<any[]>(`/sites/${site.id}/geo/prompts`), seo.get<any[]>(`/sites/${site.id}/geo/checks`), seo.get(`/sites/${site.id}/geo/gaps`),
    ]);
    return { vis, prompts, checks, gaps };
  }, [site.id]);
  const [np, setNp] = useState({ prompt: "", cluster: "" });
  const [obs, setObs] = useState({ prompt_id: "", engine: "chatgpt", answer_text: "", citations: "" });
  const [busy, setBusy] = useState(false);
  if (d.loading && !d.data) return <Loading />;
  if (!d.data) return null;
  const { vis, prompts, checks, gaps } = d.data;
  const promptText = (id: string) => prompts.find((p) => p.id === id)?.prompt || "";
  const runChecks = async () => { setBusy(true); await run("Engines asked", async () => { const r = await seo.post(`/sites/${site.id}/geo/checks/run`); return r; }); setBusy(false); d.reload(); };
  return (
    <>
      <Explain title="Read this as a measurement, not a promise.">{vis.disclaimer}</Explain>
      <div className="grid cols-4 mb">
        <Kpi label="Visibility score" value={vis.overall.checks ? vis.overall.score : "—"} sub={`${vis.overall.checks} checks · ${vis.window_days} days`} />
        <Kpi label="Mentioned" value={fmtPct(vis.overall.mention_rate, 0)} sub={vis.overall.avg_position ? `avg position ${vis.overall.avg_position}` : undefined} />
        <Kpi label="Cited (linked)" value={fmtPct(vis.overall.citation_rate, 0)} />
        <Kpi label="Share of voice" value={fmtPct(vis.share_of_voice, 0)} sub={vis.trend_delta !== null ? `${vis.trend_delta > 0 ? "+" : ""}${vis.trend_delta} vs before` : undefined} />
      </div>
      <div className="split mb">
        <div className="card">
          <h3>By engine</h3>
          {!Object.keys(vis.by_engine).length ? <div className="empty">No observations yet.</div> : (
            <table className="mt"><thead><tr><th>Engine</th><th>Checks</th><th>Mentioned</th><th>Cited</th><th>Score</th></tr></thead>
              <tbody>{Object.entries(vis.by_engine).map(([e, v]: [string, any]) => <tr key={e}><td>{ENGINES.find(([k]) => k === e)?.[1] || e}</td><td>{v.checks}</td><td>{fmtPct(v.mention_rate, 0)}</td><td>{fmtPct(v.citation_rate, 0)}</td><td><b>{v.score}</b></td></tr>)}</tbody></table>
          )}
        </div>
        <div className="card">
          <h3>Who else is named</h3>
          {!Object.keys(vis.competitor_mentions).length ? <div className="empty">No competitors named yet.</div> : <div className="mt"><Bars rows={Object.entries(vis.competitor_mentions) as [string, number][]} /></div>}
          <div className="row mt">{Object.entries(vis.sentiment || {}).map(([k, v]) => <Badge key={k} kind={k === "positive" ? "ok" : k === "negative" ? "bad" : ""}>{k} {String(v)}</Badge>)}</div>
        </div>
      </div>
      <div className="split mb">
        <div className="card">
          <div className="row between"><h3>Tracked prompts</h3><button className="sm" disabled={busy || !prompts.some((p) => p.active)} onClick={runChecks}>{busy ? "Asking…" : "Ask each engine now"}</button></div>
          <div className="sub">Asks every active prompt on the engines connected under Setup (AI answer engines). Without that connection, record answers by hand below.</div>
          <table className="mt"><tbody>{prompts.map((p) => (
            <tr key={p.id}><td>{p.prompt}{p.cluster && <span className="muted"> · {p.cluster}</span>}</td>
              <td><label className="row"><input type="checkbox" checked={p.active} onChange={(e) => run(e.target.checked ? "Tracking" : "Paused", () => seo.patch(`/geo/prompts/${p.id}`, { active: e.target.checked })).then(d.reload)} /> {p.active ? "active" : "paused"}</label></td></tr>
          ))}</tbody></table>
          <div className="row mt">
            <input style={{ flex: 1 }} placeholder="best car subscription in Miami" value={np.prompt} onChange={(e) => setNp({ ...np, prompt: e.target.value })} />
            <input placeholder="cluster (optional)" value={np.cluster} onChange={(e) => setNp({ ...np, cluster: e.target.value })} />
            <button className="ghost" disabled={!np.prompt} onClick={() => run("Prompt tracked", () => seo.post(`/sites/${site.id}/geo/prompts`, np)).then(() => { setNp({ prompt: "", cluster: "" }); d.reload(); })}>Track</button>
          </div>
        </div>
        <div className="card">
          <h3>Record an AI answer</h3>
          <div className="form mt">
            <label className="field">Prompt<select value={obs.prompt_id} onChange={(e) => setObs({ ...obs, prompt_id: e.target.value })}><option value="">— choose —</option>{prompts.map((p) => <option key={p.id} value={p.id}>{p.prompt}</option>)}</select></label>
            <label className="field">Engine<select value={obs.engine} onChange={(e) => setObs({ ...obs, engine: e.target.value })}>{ENGINES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          </div>
          <textarea className="mt" style={{ minHeight: 70, fontFamily: "inherit" }} placeholder="Paste the engine's answer" value={obs.answer_text} onChange={(e) => setObs({ ...obs, answer_text: e.target.value })} />
          <input className="mt" style={{ width: "100%" }} placeholder="Cited URLs, comma-separated" value={obs.citations} onChange={(e) => setObs({ ...obs, citations: e.target.value })} />
          <button className="mt" disabled={!obs.prompt_id || !obs.answer_text} onClick={() => run("Observation recorded", () => seo.post(`/sites/${site.id}/geo/checks`, { ...obs, citations: obs.citations.split(/[\s,]+/).filter(Boolean) })).then(() => { setObs({ ...obs, answer_text: "", citations: "" }); d.reload(); })}>Analyse and record</button>
        </div>
      </div>
      <div className="split mb">
        <div className="card">
          <div className="row between"><h3>Answers that name competitors, not you</h3><button className="sm ghost" onClick={() => run("Optimisation tasks queued", async () => { const r = await seo.post(`/sites/${site.id}/geo/optimize`); return r; }).then(d.reload)}>Queue fixes</button></div>
          {!gaps.gaps.length ? <div className="empty">None found.</div> : <table className="mt"><tbody>{gaps.gaps.map((g: any, i: number) => <tr key={i}><td>{g.prompt || promptText(g.prompt_id)}</td><td>{ENGINES.find(([k]) => k === g.engine)?.[1] || g.engine}</td><td className="muted">{(g.competitors_named || []).join(", ") || "nobody named"}</td></tr>)}</tbody></table>}
        </div>
        <div className="card">
          <h3>Sources the engines cite when you&apos;re absent</h3>
          {!gaps.source_targets.length ? <div className="empty">None yet.</div> : <table className="mt"><tbody>{gaps.source_targets.map((s: any) => <tr key={s.domain}><td className="mono">{s.domain}</td><td className="muted">cited {s.times_cited}×</td></tr>)}</tbody></table>}
          <div className="muted mt" style={{ fontSize: 12 }}>{gaps.disclaimer}</div>
        </div>
      </div>
      <div className="card">
        <h3>Recent observations</h3>
        {!checks.length ? <div className="empty">No observations yet.</div> : (
          <div className="table-wrap"><table className="mt"><tbody>{checks.slice(0, 50).map((c) => (
            <tr key={c.id}><td className="muted" style={{ whiteSpace: "nowrap", fontSize: 12 }}>{fmtTime(c.checked_at)}</td><td>{ENGINES.find(([k]) => k === c.engine)?.[1] || c.engine}</td><td>{promptText(c.prompt_id)}<div className="muted clip" style={{ fontSize: 12 }}>{c.answer_excerpt}</div></td>
              <td>{c.brand_mentioned ? <Badge kind="ok">mentioned #{c.mention_position}</Badge> : <Badge kind="bad">not mentioned</Badge>}{c.domain_cited && <Badge kind="ok">cited</Badge>}</td>
              <td className="muted" style={{ fontSize: 12 }}>{c.competitors_mentioned.join(", ")}</td></tr>
          ))}</tbody></table></div>
        )}
      </div>
    </>
  );
}

// ── Links & authority ─────────────────────────────────────────────────
const NEXT_STATUS: Record<string, string[]> = { prospect: ["approved_for_outreach", "rejected"], approved_for_outreach: ["contacted", "rejected"], contacted: ["replied", "rejected", "lost"], replied: ["acquired", "rejected", "lost"], acquired: ["lost"] };

export function Links() {
  return <SeoPage title="Links & authority" text="Prospects found from the sites that link to competitors, compliant outreach, the backlinks won and lost, and authority over time.">{(site) => <LinksBody site={site} />}</SeoPage>;
}

function LinksBody({ site }: { site: Site }) {
  const run = useAction();
  const d = useLoad(async () => {
    const [gate, prospects, outreach, backlinks, authority] = await Promise.all([
      seo.get("/links/gate"), seo.get<any[]>(`/sites/${site.id}/links/prospects`), seo.get<any[]>(`/sites/${site.id}/links/outreach`), seo.get<any[]>(`/sites/${site.id}/links/backlinks`), seo.get(`/sites/${site.id}/authority`),
    ]);
    return { gate, prospects, outreach, backlinks, authority };
  }, [site.id]);
  const [np, setNp] = useState({ domain: "", url: "", contact_email: "", link_type: "editorial", notes: "" });
  const [draft, setDraft] = useState<{ p: any; subject: string; body: string } | null>(null);
  const [busy, setBusy] = useState(false);
  if (d.loading && !d.data) return <Loading />;
  if (!d.data) return null;
  const { gate, prospects, outreach, backlinks, authority } = d.data;
  const discover = async () => { setBusy(true); await run("Prospects discovered", async () => { const r = await seo.post(`/sites/${site.id}/links/discover`, {}); return r; }); setBusy(false); d.reload(); };
  return (
    <>
      <Explain title="The link-scheme gate.">Never pursued: {gate.blocked_link_types.map(pretty).join(", ")}. A prospect with spam score ≥ {gate.spam_score_block} is blocked; outreach needs relevance ≥ {gate.min_relevance}, an opt-out line, and respects the suppression list. Sponsored placements are only started by a person and carry rel=sponsored.</Explain>
      <div className="grid cols-4 mb">
        <Kpi label="Authority" value={authority.latest?.authority_score ?? "—"} sub={authority.growth_pct_30d !== null ? `${authority.growth_pct_30d}% in 30 days` : authority.latest?.source} />
        <Kpi label="Referring domains" value={fmtN(authority.latest?.referring_domains)} />
        <Kpi label="Backlinks tracked" value={fmtN(authority.backlinks_tracked)} sub={`${authority.active} active · ${authority.lost} lost · ${authority.dofollow} dofollow`} />
        <Kpi label="Prospects" value={prospects.length} sub={`${prospects.filter((p) => p.status === "blocked").length} blocked by the gate`} />
      </div>
      {authority.series.length > 1 && <div className="card mb"><h3>Authority over time</h3><LineChart points={authority.series.map((s: any) => ({ x: s.on, y: s.authority_score }))} label="Authority score" /><div className="muted" style={{ fontSize: 12 }}>{authority.metric_note}</div></div>}
      {draft && (
        <div className="card mb" style={{ borderColor: "var(--gold)" }}>
          <h3>Outreach to {draft.p.domain}</h3>
          <div className="sub">To {draft.p.contact_email}. An opt-out line is required; the suppression list is checked before sending.</div>
          <input className="mt" style={{ width: "100%" }} placeholder="Subject" value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
          <textarea className="mt" style={{ minHeight: 120, fontFamily: "inherit" }} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
          <div className="row mt">
            <button disabled={!draft.subject || !draft.body} onClick={() => run("Outreach sent", () => seo.post(`/links/prospects/${draft.p.id}/outreach`, { subject: draft.subject, body: draft.body })).then((ok) => { if (ok) { setDraft(null); d.reload(); } })}>Send</button>
            <button className="ghost" onClick={() => setDraft(null)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="card mb">
        <div className="row between"><h3>Prospects</h3><button className="sm" disabled={busy} onClick={discover}>{busy ? "Looking…" : "Discover from competitors"}</button></div>
        {!prospects.length ? <div className="empty">No prospects yet.</div> : (
          <div className="table-wrap"><table className="mt">
            <thead><tr><th>Site</th><th>Type</th><th>Relevance</th><th>Authority</th><th>Spam</th><th>Score</th><th>Status</th><th /></tr></thead>
            <tbody>{prospects.map((p) => (
              <tr key={p.id}>
                <td><b className="mono">{p.domain}</b>{p.contact_email && <div className="muted" style={{ fontSize: 12 }}>{p.contact_email}</div>}{p.notes && <div className="muted clip" style={{ fontSize: 12 }}>{p.notes}</div>}</td>
                <td>{pretty(p.link_type)}</td><td>{p.relevance}</td><td>{p.authority}</td><td style={{ color: p.spam_score >= gate.spam_score_block ? "var(--bad)" : undefined }}>{p.spam_score}</td><td><b>{p.score}</b></td>
                <td><StatusBadge value={p.status} />{p.block_reason && <div style={{ color: "var(--bad)", fontSize: 12 }}>{p.block_reason}</div>}</td>
                <td><div className="row">
                  {p.status === "approved_for_outreach" && <button className="sm" disabled={!p.contact_email} title={p.contact_email ? "" : "No contact email"} onClick={() => setDraft({ p, subject: `A resource for your ${p.domain} readers`, body: `Hi,\n\n…\n\nIf you'd rather not hear from us, reply "unsubscribe" and we won't contact you again.` })}>Draft outreach</button>}
                  {(NEXT_STATUS[p.status] || []).map((s) => <button key={s} className="sm ghost" onClick={() => run(`Moved to ${pretty(s)}`, () => seo.post(`/links/prospects/${p.id}/status`, { status: s })).then(d.reload)}>{s === "approved_for_outreach" ? "Approve" : pretty(s)}</button>)}
                </div></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
        <h4 className="mt">Add a prospect</h4>
        <div className="form">
          <label className="field">Domain<input value={np.domain} placeholder="autotrends.com" onChange={(e) => setNp({ ...np, domain: e.target.value })} /></label>
          <label className="field">Page URL<input value={np.url} onChange={(e) => setNp({ ...np, url: e.target.value })} /></label>
          <label className="field">Contact email<input value={np.contact_email} onChange={(e) => setNp({ ...np, contact_email: e.target.value })} /></label>
          <label className="field">Type<select value={np.link_type} onChange={(e) => setNp({ ...np, link_type: e.target.value })}>{gate.allowed_link_types.map((t: string) => <option key={t} value={t}>{pretty(t)}</option>)}</select></label>
        </div>
        <button className="mt" disabled={!np.domain} onClick={() => run("Prospect added", () => seo.post(`/sites/${site.id}/links/prospects`, np)).then(() => { setNp({ domain: "", url: "", contact_email: "", link_type: "editorial", notes: "" }); d.reload(); })}>Add</button>
      </div>
      <div className="split">
        <div className="card"><h3>Outreach log</h3>
          {!outreach.length ? <div className="empty">Nothing sent.</div> : <table className="mt"><tbody>{outreach.map((o) => <tr key={o.id}><td className="muted" style={{ fontSize: 12 }}>{fmtTime(o.created_at)}</td><td>{o.to_contact}<div className="muted clip">{o.subject}</div></td><td><StatusBadge value={o.status} />{o.delivery_mode === "console" && <Badge kind="warn">console only</Badge>}</td></tr>)}</tbody></table>}
        </div>
        <div className="card"><h3>Backlinks</h3>
          {!backlinks.length ? <div className="empty">None tracked yet.</div> : <table className="mt"><tbody>{backlinks.map((b) => <tr key={b.id}><td className="mono" style={{ fontSize: 12 }}>{b.source_domain}</td><td className="muted clip" style={{ fontSize: 12 }}>{b.anchor}</td><td><Badge>{b.rel}</Badge></td><td><StatusBadge value={b.status} /></td></tr>)}</tbody></table>}
        </div>
      </div>
    </>
  );
}

// ── Conversion (CRO) ──────────────────────────────────────────────────
export function Cro() {
  return <SeoPage title="Conversion (CRO)" text="Reviews of the pages that matter for what is costing conversions, and A/B experiments that end only on a statistically significant result.">{(site) => <CroBody site={site} />}</SeoPage>;
}

function CroBody({ site }: { site: Site }) {
  const run = useAction();
  const findings = useLoad(() => seo.get<any[]>(`/sites/${site.id}/cro/findings`), [site.id]);
  const exps = useLoad(() => seo.get<any[]>(`/sites/${site.id}/cro/experiments`), [site.id]);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [nx, setNx] = useState({ page_url: "", element: "cta", hypothesis: "", a: "", b: "", finding_id: "" });
  const [res, setRes] = useState<Record<string, any>>({});
  const [cta, setCta] = useState("");
  const [score, setScore] = useState<any>(null);
  const reloadAll = () => { findings.reload(); exps.reload(); };
  const review = async () => { setBusy(true); await run("Pages reviewed", async () => { const r = await seo.post(`/sites/${site.id}/cro/findings/run`, url ? { url } : {}); return r; }); setBusy(false); reloadAll(); };
  const open = (findings.data || []).filter((f) => f.status === "open");
  return (
    <>
      <div className="row mb">
        <input style={{ minWidth: 320 }} placeholder={`One page (optional) — https://${site.domain}/pricing`} value={url} onChange={(e) => setUrl(e.target.value)} />
        <button disabled={busy} onClick={review}>{busy ? "Reviewing…" : "Review pages"}</button>
      </div>
      <div className="card mb">
        <h3>What is costing conversions</h3>
        {findings.loading ? <Loading /> : !open.length ? <div className="empty">No open findings.</div> : (
          <div className="table-wrap"><table className="mt">
            <thead><tr><th>Finding</th><th>Page</th><th>Change</th><th /></tr></thead>
            <tbody>{open.map((f) => (
              <tr key={f.id}>
                <td><Badge kind={sev(f.severity)}>{f.severity}</Badge> <b>{pretty(f.code)}</b><div className="muted" style={{ fontSize: 12 }}>{f.detail}</div></td>
                <td className="mono clip" style={{ fontSize: 12 }}>{f.page_url}</td>
                <td style={{ fontSize: 12.5 }}>{f.recommendation?.change}<div className="muted">{f.recommendation?.rationale}</div></td>
                <td><div className="row">
                  <button className="sm" onClick={() => setNx({ page_url: f.page_url, element: f.code.includes("cta") ? "cta" : "headline", hypothesis: f.recommendation?.test_idea || "", a: "", b: "", finding_id: f.id })}>Test it</button>
                  <button className="sm ghost" onClick={() => run("Dismissed", () => seo.post(`/cro/findings/${f.id}/dismiss`)).then(findings.reload)}>Dismiss</button>
                </div></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
      <div className="split mb">
        <div className="card">
          <div className="row between"><h3>Start an experiment</h3><button className="sm ghost" onClick={() => run("Next experiment chosen", async () => { const r = await seo.post(`/sites/${site.id}/cro/next-experiment`); return r; }).then(reloadAll)}>Let the agent pick</button></div>
          <div className="form mt">
            <label className="field">Page<input value={nx.page_url} onChange={(e) => setNx({ ...nx, page_url: e.target.value })} /></label>
            <label className="field">Element<select value={nx.element} onChange={(e) => setNx({ ...nx, element: e.target.value })}>{["cta", "headline", "hero", "form", "pricing", "social_proof"].map((x) => <option key={x}>{x}</option>)}</select></label>
          </div>
          <input className="mt" style={{ width: "100%" }} placeholder="Hypothesis" value={nx.hypothesis} onChange={(e) => setNx({ ...nx, hypothesis: e.target.value })} />
          <div className="form mt">
            <label className="field">Variant A (control)<input value={nx.a} onChange={(e) => setNx({ ...nx, a: e.target.value })} /></label>
            <label className="field">Variant B<input value={nx.b} onChange={(e) => setNx({ ...nx, b: e.target.value })} /></label>
          </div>
          <button className="mt" disabled={!nx.page_url || !nx.a || !nx.b} onClick={() => run("Experiment started", () => seo.post(`/sites/${site.id}/cro/experiments`, { page_url: nx.page_url, element: nx.element, hypothesis: nx.hypothesis, variant_a: { [nx.element]: nx.a }, variant_b: { [nx.element]: nx.b }, finding_id: nx.finding_id || undefined })).then((ok) => { if (ok) { setNx({ page_url: "", element: "cta", hypothesis: "", a: "", b: "", finding_id: "" }); reloadAll(); } })}>Start</button>
        </div>
        <div className="card">
          <h3>Score a call to action</h3>
          <div className="row mt"><input style={{ flex: 1 }} placeholder="Get started" value={cta} onChange={(e) => setCta(e.target.value)} /><button className="ghost" disabled={!cta} onClick={() => run("Scored", async () => setScore(await seo.post("/cro/cta-strength", { text: cta })))}>Score</button></div>
          {score && <>
            <div className="mt"><b style={{ fontSize: 22, color: score.strength >= 70 ? "var(--ok)" : score.strength >= 40 ? "var(--warn)" : "var(--bad)" }}>{score.strength}</b><span className="muted">/100</span></div>
            <div className="muted mt">Stronger options:</div>
            <ul style={{ margin: "4px 0", paddingLeft: 18 }}>{score.variants.map((v: string) => <li key={v}>{v}</li>)}</ul>
          </>}
        </div>
      </div>
      <div className="card">
        <h3>Experiments</h3>
        <div className="sub">An experiment ends only on a significant result (p &lt; 0.05 with enough visitors per arm); before that it reports “insufficient data” and keeps running.</div>
        {exps.loading ? <Loading /> : !(exps.data || []).length ? <div className="empty">No experiments yet.</div> : (exps.data || []).map((x) => {
          const r = res[x.id] || { a_visitors: x.visitors_a, a_conversions: x.conversions_a, b_visitors: x.visitors_b, b_conversions: x.conversions_b };
          const set = (k: string, v: string) => setRes({ ...res, [x.id]: { ...r, [k]: Number(v) } });
          return (
            <div key={x.id} className="card mt" style={{ background: "var(--panel-2)" }}>
              <div className="row between"><div><b>{pretty(x.element)}</b> on <span className="mono" style={{ fontSize: 12 }}>{x.page_url}</span><div className="muted" style={{ fontSize: 12 }}>{x.hypothesis}</div></div><div className="row"><StatusBadge value={x.status} />{x.winner && <Badge kind="ok">winner {x.winner.toUpperCase()}</Badge>}{x.p_value !== null && <Badge>p = {x.p_value}</Badge>}</div></div>
              {x.guardrail_findings.length > 0 && <ul className="findings">{x.guardrail_findings.map((g: string) => <li key={g}>{g}</li>)}</ul>}
              <table className="mt"><thead><tr><th>Arm</th><th>Copy</th><th>Visitors</th><th>Conversions</th><th>Rate</th></tr></thead><tbody>
                {(["a", "b"] as const).map((arm) => {
                  const v = r[`${arm}_visitors`], c = r[`${arm}_conversions`];
                  return <tr key={arm}><td><b>{arm.toUpperCase()}</b></td><td>{Object.values(arm === "a" ? x.variant_a : x.variant_b).join(" · ")}</td>
                    <td>{x.status === "running" ? <input type="number" min={0} style={{ width: 90 }} value={v} onChange={(e) => set(`${arm}_visitors`, e.target.value)} /> : v}</td>
                    <td>{x.status === "running" ? <input type="number" min={0} style={{ width: 90 }} value={c} onChange={(e) => set(`${arm}_conversions`, e.target.value)} /> : c}</td>
                    <td>{v ? fmtPct(c / v) : "—"}</td></tr>;
                })}
              </tbody></table>
              {x.status === "running" && <div className="row mt">
                <button className="sm ghost" onClick={() => run("Results saved", () => seo.post(`/cro/experiments/${x.id}/results`, r)).then(exps.reload)}>Save results</button>
                <button className="sm" onClick={() => run("Evaluated", async () => { await seo.post(`/cro/experiments/${x.id}/results`, r); const e = await seo.post(`/cro/experiments/${x.id}/evaluate`); if (e.decision === "insufficient_data") throw new Error(`Not enough data yet — ${e.needed_per_arm} visitors per arm needed.`); }).then(exps.reload)}>Evaluate</button>
              </div>}
            </div>
          );
        })}
      </div>
    </>
  );
}

// ── Local SEO ─────────────────────────────────────────────────────────
const EMPTY_LOC = { name: "", street: "", city: "", region: "", postal: "", phone: "", website: "", categories: "", services: "" };

export function Local() {
  return <SeoPage title="Local SEO" text="Google Business Profile completeness per location, name/address/phone consistency across directories, and local-pack rankings.">{(site) => <LocalBody site={site} />}</SeoPage>;
}

function LocalBody({ site }: { site: Site }) {
  const run = useAction();
  const d = useLoad(async () => {
    const [locations, keywords, rank, checklist] = await Promise.all([
      seo.get<any[]>(`/sites/${site.id}/local/locations`), seo.get<any[]>(`/sites/${site.id}/local/keywords`), seo.get(`/sites/${site.id}/local/rank`), seo.get<any[]>("/local/checklist"),
    ]);
    return { locations, keywords, rank, checklist };
  }, [site.id]);
  const [f, setF] = useState(EMPTY_LOC);
  const [open, setOpen] = useState<string | null>(null);
  const [nap, setNap] = useState<Record<string, any>>({});
  const [change, setChange] = useState({ item: "description", value: "" });
  if (d.loading && !d.data) return <Loading />;
  if (!d.data) return null;
  const { locations, keywords, rank, checklist } = d.data;
  const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const add = () => run("Location added", () => seo.post(`/sites/${site.id}/local/locations`, {
    name: f.name, address: { street: f.street, city: f.city, region: f.region, postal: f.postal }, phone: f.phone, website: f.website, categories: list(f.categories), services: list(f.services),
  })).then((ok) => { if (ok) { setF(EMPTY_LOC); d.reload(); } });
  const agentItems = checklist.filter((c) => c.agent_can_do).map((c) => c.item);
  return (
    <>
      <Explain title="What may change automatically.">Agents may update only {agentItems.map(pretty).join(", ")}. A location&apos;s name, address, phone, categories, photos and reviews are changed by a person, on the profile itself.</Explain>
      <div className="grid cols-4 mb">
        <Kpi label="Locations" value={locations.length} />
        <Kpi label="Avg profile score" value={locations.length ? Math.round(locations.reduce((a, l) => a + l.gbp_score, 0) / locations.length) : "—"} />
        <Kpi label="In the map pack" value={fmtPct(rank.pack_rate, 0)} sub={`${rank.in_pack} of ${rank.tracked} keyword checks`} />
        <Kpi label="Local keywords" value={keywords.length} />
      </div>
      <div className="row mb"><button className="ghost" onClick={() => run("Locations audited", async () => { const r = await seo.post(`/sites/${site.id}/local/audit`); return r; }).then(d.reload)}>Audit all locations</button></div>
      {locations.map((l) => (
        <div key={l.id} className="card mb">
          <div className="row between">
            <div><b>{l.name}</b><div className="muted" style={{ fontSize: 12 }}>{[l.address?.street, l.address?.city, l.address?.region, l.address?.postal].filter(Boolean).join(", ")} · {l.phone}</div></div>
            <div className="row"><Badge kind={l.gbp_score >= 80 ? "ok" : l.gbp_score >= 50 ? "warn" : "bad"}>profile {l.gbp_score}/100</Badge><button className="sm ghost" onClick={() => setOpen(open === l.id ? null : l.id)}>{open === l.id ? "Close" : "Open"}</button></div>
          </div>
          <div className="bar mt"><div style={{ width: `${l.gbp_score}%` }} /></div>
          {open === l.id && (
            <div className="split mt">
              <div>
                <h4>To complete the profile</h4>
                <table><tbody>{l.gbp_missing.map((m: any) => <tr key={m.item}><td><b>{pretty(m.item)}</b><div className="muted" style={{ fontSize: 12 }}>{m.how}</div></td><td className="muted">+{m.weight}</td><td>{m.agent_can_do ? <Badge kind="ai">agent can do</Badge> : <Badge>person</Badge>}</td></tr>)}</tbody></table>
                <div className="row mt">
                  <button className="sm ghost" onClick={() => run("Audited", () => seo.post(`/local/locations/${l.id}/audit`)).then(d.reload)}>Re-audit</button>
                  <button className="sm ghost" onClick={() => run("Checked", async () => setNap({ ...nap, [l.id]: await seo.get(`/local/locations/${l.id}/nap`) }))}>Check NAP consistency</button>
                </div>
                {nap[l.id] && <div className="mt">{nap[l.id].citations ? <><b>{nap[l.id].consistent}/{nap[l.id].citations}</b> directory listings match ({nap[l.id].consistency_pct}%)
                  <table className="mt"><tbody>{nap[l.id].details.map((x: any, i: number) => <tr key={i}><td>{x.directory}</td><td>{x.consistent ? <Badge kind="ok">matches</Badge> : <Badge kind="bad">differs</Badge>}</td><td className="muted" style={{ fontSize: 12 }}>{[...(x.mismatched || []).map((m: string) => `${m} differs`), ...(x.missing || []).map((m: string) => `${m} missing`)].join(", ")}</td></tr>)}</tbody></table></> : <span className="muted">No directory citations recorded for this location.</span>}</div>}
              </div>
              <div>
                <h4>Update the profile</h4>
                <div className="form">
                  <label className="field">Item<select value={change.item} onChange={(e) => setChange({ ...change, item: e.target.value })}>{agentItems.map((i) => <option key={i} value={i}>{pretty(i)}</option>)}</select></label>
                </div>
                <textarea className="mt" style={{ minHeight: 90, fontFamily: "inherit" }} placeholder={change.item === "services" ? "Car subscription, EV rental, Airport delivery" : change.item === "description" ? "At least 250 characters about what you offer and where" : ""} value={change.value} onChange={(e) => setChange({ ...change, value: e.target.value })} />
                {change.item === "description" && <div className={change.value.length < 250 ? "counter over" : "counter"}>{change.value.length}/250 minimum</div>}
                <button className="sm mt" disabled={!change.value} onClick={() => run("Profile updated", () => seo.post(`/local/locations/${l.id}/profile`, { item: change.item, value: change.item === "services" ? list(change.value) : change.value })).then((ok) => { if (ok) { setChange({ ...change, value: "" }); d.reload(); } })}>Save</button>
                <div className="muted mt" style={{ fontSize: 12 }}>With a Google Business Profile connection the change goes live; otherwise it is kept as a pending change for a person to copy.</div>
              </div>
            </div>
          )}
        </div>
      ))}
      <div className="split mb">
        <div className="card">
          <h3>Add a location</h3>
          <div className="form mt">
            <label className="field">Name<input value={f.name} placeholder="VOYQI Miami Hub" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
            <label className="field">Street<input value={f.street} onChange={(e) => setF({ ...f, street: e.target.value })} /></label>
            <label className="field">City<input value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></label>
            <label className="field">Region<input value={f.region} onChange={(e) => setF({ ...f, region: e.target.value })} /></label>
            <label className="field">Postal code<input value={f.postal} onChange={(e) => setF({ ...f, postal: e.target.value })} /></label>
            <label className="field">Phone<input value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></label>
            <label className="field">Website<input value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></label>
            <label className="field">Categories<input value={f.categories} placeholder="Car rental agency, Car leasing service" onChange={(e) => setF({ ...f, categories: e.target.value })} /></label>
            <label className="field">Services<input value={f.services} placeholder="Car subscription, EV rental" onChange={(e) => setF({ ...f, services: e.target.value })} /></label>
          </div>
          <button className="mt" disabled={!f.name || !f.city} onClick={add}>Add location</button>
        </div>
        <div className="card">
          <h3>Local keywords worth tracking</h3>
          {!keywords.length ? <div className="empty">Add a location first.</div> : <table className="mt"><tbody>{keywords.map((k, i) => <tr key={i}><td>{k.keyword}</td><td className="muted">{k.location}</td></tr>)}</tbody></table>}
          {rank.locations.length > 0 && <>
            <h4 className="mt">Map-pack rank</h4>
            <table><tbody>{rank.locations.map((l: any) => (
              <Fragment key={l.location_id}>{l.keywords.map((k: any, i: number) => <tr key={i}><td>{l.name}</td><td>{k.keyword}</td><td>{k.in_pack ? <Badge kind="ok">#{k.position ?? "in pack"}</Badge> : <span className="muted">{k.position ? `#${k.position}` : "not in pack"}</span>}</td></tr>)}</Fragment>
            ))}</tbody></table>
          </>}
        </div>
      </div>
    </>
  );
}

