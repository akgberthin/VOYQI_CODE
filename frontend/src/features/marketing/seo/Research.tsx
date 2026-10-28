"use client";

import { useState } from "react";
import { Badge, Empty, Loading, pretty, useAction, useLoad } from "@/lib/ui";
import { Explain, SeoPage, fmtN, seo, type Site } from "./seo";

export function Research() {
  return <SeoPage title="Research & keywords" text="Keywords for the site and its competitors, the gaps where competitors rank and you don't, the questions buyers ask, and what search results, communities, news and video say.">{(site) => <Body site={site} />}</SeoPage>;
}

function Body({ site }: { site: Site }) {
  const run = useAction();
  const [f, setF] = useState({ view: "all", intent: "", min_purchase_intent: "", owner: "" });
  const qs = new URLSearchParams({ ...(f.view === "gaps" ? { gaps_only: "true" } : f.view === "questions" ? { questions_only: "true" } : {}), ...(f.intent ? { intent: f.intent } : {}), ...(f.min_purchase_intent ? { min_purchase_intent: f.min_purchase_intent } : {}), ...(f.owner ? { owner: f.owner } : {}) }).toString();
  const kw = useLoad(() => seo.get<any[]>(`/sites/${site.id}/keywords${qs ? `?${qs}` : ""}`), [site.id, qs]);
  const d = useLoad(async () => {
    const [questions, competitors, sources, digest] = await Promise.all([
      seo.get<any[]>(`/sites/${site.id}/keywords/questions`), seo.get<any[]>(`/sites/${site.id}/competitors`), seo.get<any[]>(`/sites/${site.id}/research/sources`), seo.get(`/sites/${site.id}/research/digest`),
    ]);
    return { questions, competitors, sources, digest };
  }, [site.id]);
  const [src, setSrc] = useState("");
  const items = useLoad(() => seo.get<any[]>(`/sites/${site.id}/research/items${src ? `?source=${src}` : ""}`), [site.id, src]);
  const [comp, setComp] = useState("");
  const [query, setQuery] = useState("");
  const [intent, setIntent] = useState<any>(null);
  const [imp, setImp] = useState("");
  const [busy, setBusy] = useState("");
  const reloadAll = () => { kw.reload(); d.reload(); items.reload(); };
  const act = async (key: string, label: string, fn: () => Promise<any>) => { setBusy(key); await run(label, fn); setBusy(""); reloadAll(); };
  const importKw = () => act("import", "Keywords imported", async () => {
    const keywords = imp.split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((p) => p[0]).map(([term, volume, difficulty]) => ({ term, volume: Number(volume) || 0, difficulty: Number(difficulty) || 0 }));
    await seo.post(`/sites/${site.id}/keywords/import`, { keywords, source: "console import" });
    setImp("");
  });
  const digest = d.data?.digest;
  return (
    <>
      <div className="row mb">
        <button disabled={!!busy} onClick={() => act("kw", "Keywords refreshed", async () => { const r = await seo.post(`/sites/${site.id}/keywords/sync`); return r; })}>{busy === "kw" ? "Refreshing…" : "Refresh keyword data"}</button>
        <button className="ghost" disabled={!!busy} onClick={() => act("research", "Research gathered", () => seo.post(`/sites/${site.id}/research/run`, {}))}>{busy === "research" ? "Researching…" : "Run research"}</button>
      </div>
      <div className="card mb">
        <div className="row between">
          <h3>Keywords</h3>
          <div className="row">
            <div className="seg">{[["all", "All"], ["gaps", "Gaps"], ["questions", "Questions"]].map(([k, l]) => <button key={k} className={f.view === k ? "on" : ""} onClick={() => setF({ ...f, view: k })}>{l}</button>)}</div>
            <select value={f.intent} onChange={(e) => setF({ ...f, intent: e.target.value })}><option value="">any intent</option>{["transactional", "commercial", "informational", "navigational", "local"].map((i) => <option key={i}>{i}</option>)}</select>
            <select value={f.min_purchase_intent} onChange={(e) => setF({ ...f, min_purchase_intent: e.target.value })}><option value="">any buying intent</option><option value="50">buying intent ≥ 50</option><option value="75">buying intent ≥ 75</option></select>
            <select value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })}><option value="">everyone</option><option value={site.domain}>{site.domain}</option>{(d.data?.competitors || []).map((c) => <option key={c.id} value={c.domain}>{c.domain}</option>)}</select>
          </div>
        </div>
        {f.view === "gaps" && <div className="sub mt">A gap is a term a competitor ranks for in the top 20 and this site doesn&apos;t rank for at all, weighted by volume and buying intent.</div>}
        {kw.loading ? <Loading /> : !(kw.data || []).length ? <div className="empty">No keywords yet — refresh keyword data.</div> : (
          <div className="table-wrap"><table className="mt">
            <thead><tr><th>Term</th><th>Owner</th><th>Volume</th><th>Difficulty</th><th>Position</th><th>Intent</th><th>Buying intent</th><th>Trend</th><th>Opportunity</th></tr></thead>
            <tbody>{(kw.data || []).map((k) => (
              <tr key={k.id}>
                <td><b>{k.term}</b>{k.is_gap && <Badge kind="warn">gap</Badge>}{k.is_question && <Badge kind="info">question</Badge>}</td>
                <td className="muted">{k.owner}</td><td>{fmtN(k.volume)}</td><td>{k.difficulty}</td><td>{k.position ?? "—"}</td><td>{k.intent}</td><td>{k.purchase_intent}</td>
                <td>{k.trend === "rising" ? <span style={{ color: "var(--ok)" }}>▲ {k.growth_pct}%</span> : k.trend === "falling" ? <span style={{ color: "var(--bad)" }}>▼ {k.growth_pct}%</span> : <span className="muted">flat</span>}</td>
                <td><b>{k.opportunity}</b></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
        <details className="mt"><summary className="muted">Import keywords from your own tool</summary>
          <textarea className="mt" style={{ minHeight: 70 }} placeholder={"term, volume, difficulty — one per line\ncar subscription miami, 880, 32"} value={imp} onChange={(e) => setImp(e.target.value)} />
          <button className="sm mt" disabled={!imp.trim() || !!busy} onClick={importKw}>Import</button>
        </details>
      </div>
      <div className="split mb">
        <div className="card">
          <h3>Questions buyers ask</h3>
          {!(d.data?.questions || []).length ? <div className="empty">None found yet.</div> : (
            <table className="mt"><tbody>{(d.data?.questions || []).map((q, i) => <tr key={i}><td>{q.question}</td><td className="muted">{fmtN(q.volume)}/mo</td><td><Badge>buying {q.purchase_intent}</Badge></td></tr>)}</tbody></table>
          )}
        </div>
        <div className="card">
          <h3>Competitors</h3>
          <table className="mt"><tbody>{(d.data?.competitors || []).map((c) => <tr key={c.id}><td className="mono">{c.domain}</td><td className="muted">{c.notes}</td><td>{c.authority_score ?? ""}</td></tr>)}</tbody></table>
          <div className="row mt">
            <input placeholder="competitor.com" value={comp} onChange={(e) => setComp(e.target.value)} />
            <button className="ghost" disabled={!comp} onClick={() => run("Competitor added", () => seo.post(`/sites/${site.id}/competitors`, { domain: comp })).then(() => { setComp(""); d.reload(); })}>Add</button>
          </div>
        </div>
      </div>
      <div className="card mb">
        <h3>What do searchers actually want?</h3>
        <div className="row mt">
          <input style={{ flex: 1 }} placeholder="car subscription cost" value={query} onChange={(e) => setQuery(e.target.value)} />
          <button disabled={!query} onClick={() => run("Analysed", async () => setIntent(await seo.post(`/sites/${site.id}/research/search-intent`, { query })))}>Analyse intent</button>
        </div>
        {intent && (
          <div className="kv mt">
            <span>Intent</span><span><b>{intent.intent}</b> · buying intent {intent.purchase_intent} · confidence {Math.round(intent.confidence * 100)}%</span>
            <span>Results look like</span><span>{intent.dominant_format} ({Object.entries(intent.evidence?.format_counts || {}).map(([k, v]) => `${v} ${pretty(k)}`).join(", ")})</span>
            <span>Write</span><span>a {intent.recommended_content?.kind}: {intent.recommended_content?.angle}</span>
            <span>Must include</span><span>{(intent.recommended_content?.must_include || []).join(" · ")}</span>
          </div>
        )}
      </div>
      <Explain title="Not every source is used for every task.">Research from forums, news and video only runs where that source is connected. Anything that reads like instructions to an AI is quarantined and never reaches an agent.</Explain>
      <div className="split mb">
        <div className="card">
          <h3>Research sources</h3>
          <table className="mt"><tbody>{(d.data?.sources || []).map((s) => (
            <tr key={s.source}><td>{s.label}</td><td><Badge kind={s.integration_status === "connected" ? "ok" : ""}>{s.integration_status.replace(/_/g, " ")}</Badge></td><td className="muted">{s.items} items</td>
              <td>{s.integration_status === "connected" && <button className="sm ghost" disabled={!!busy} onClick={() => act(s.source, `${s.label} gathered`, () => seo.post(`/sites/${site.id}/research/run`, { sources: [s.source] }))}>Run</button>}</td></tr>
          ))}</tbody></table>
        </div>
        <div className="card">
          <h3>What the research says</h3>
          {!digest || !digest.items ? <div className="empty">Run research first.</div> : (
            <>
              <div className="muted">{digest.items} items · {digest.excluded_as_instruction_like} quarantined</div>
              <h4 className="mt">Themes</h4>
              <div className="row">{digest.themes.map((t: any) => <Badge key={t.theme}>{t.theme} · {t.mentions}</Badge>)}</div>
              <h4 className="mt">Intent mix</h4>
              <div className="row">{Object.entries(digest.search_intent || {}).map(([k, v]) => <Badge key={k} kind="info">{k} {String(v)}</Badge>)}</div>
              {digest.questions.length > 0 && <><h4 className="mt">Questions in results</h4><ul style={{ margin: "4px 0", paddingLeft: 18 }}>{digest.questions.slice(0, 6).map((q: any) => <li key={q.question}>{q.question}</li>)}</ul></>}
            </>
          )}
        </div>
      </div>
      <div className="card">
        <div className="row between"><h3>Research items</h3>
          <select value={src} onChange={(e) => setSrc(e.target.value)}><option value="">all sources</option>{(d.data?.sources || []).map((s) => <option key={s.source} value={s.source}>{s.label}</option>)}</select>
        </div>
        {items.loading ? <Loading /> : !(items.data || []).length ? <Empty>Nothing gathered yet.</Empty> : (
          <div className="table-wrap"><table className="mt"><tbody>{(items.data || []).slice(0, 100).map((i) => (
            <tr key={i.id}><td><Badge>{i.source}</Badge></td><td className="muted">{i.query}</td><td><a href={i.url} target="_blank" rel="noreferrer">{i.title}</a><div className="muted clip" style={{ fontSize: 12 }}>{i.summary}</div></td>
              <td className="muted">{i.signals?.position ? `#${i.signals.position}` : ""}</td></tr>
          ))}</tbody></table></div>
        )}
      </div>
    </>
  );
}
