"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, get, must, type ApiResult } from "@/lib/api";
import { SchemaForm, resolve, type Spec } from "../SchemaForm";
import { AutoTable, Badge, ErrorNote, JsonView, Loading, PageHead, useLoad } from "@/lib/ui";

type Op = { method: string; path: string; summary: string; tag: string; op: any };
const METHODS = ["get", "post", "put", "patch", "delete"];
// Set by the gateway from the session token; never typed by an admin.
const GATEWAY_HEADERS = new Set(["x-tenant-id", "x-user-id", "x-user-credential-id", "x-actor-id", "x-ai-initiated"]);

export default function ApiExplorer() {
  const { name = "" } = useParams<{ name: string }>();
  const { data, error, loading } = useLoad(() => must<{ openapi: any; prefixes: string[] }>(get(`/api/v1/platform/services/${name}/openapi`)), [name]);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Op | null>(null);

  const ops = useMemo<Op[]>(() => {
    const out: Op[] = [];
    for (const [path, item] of Object.entries<any>(data?.openapi?.paths || {})) {
      for (const m of METHODS) {
        if (!item[m]) continue;
        out.push({ method: m.toUpperCase(), path, summary: item[m].summary || "", tag: item[m].tags?.[0] || "other", op: { ...item[m], parameters: [...(item.parameters || []), ...(item[m].parameters || [])] } });
      }
    }
    return out;
  }, [data]);
  const shown = ops.filter((o) => `${o.method} ${o.path} ${o.summary} ${o.tag}`.toLowerCase().includes(filter.toLowerCase()));
  const tags = Array.from(new Set(shown.map((o) => o.tag)));

  return (
    <>
      <PageHead title={name} text={<><Link href="/admin/services">Services</Link> / {ops.length} APIs reachable through the gateway{data ? ` under ${data.prefixes.join(", ")}` : ""}</>} />
      <ErrorNote error={error} />
      {loading ? <Loading what="Reading the service's API description" /> : (
        <div className="explorer">
          <div className="card" style={{ padding: 8 }}>
            <input placeholder="Filter APIs…" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: "100%", marginBottom: 6 }} />
            <div className="ops">
              {tags.map((t) => (
                <div key={t}>
                  <div className="tag">{t}</div>
                  {shown.filter((o) => o.tag === t).map((o) => (
                    <div key={o.method + o.path} className={`op ${selected?.method === o.method && selected?.path === o.path ? "active" : ""}`} onClick={() => setSelected(o)}>
                      <span className={`method m-${o.method}`}>{o.method}</span>
                      <span className="p" title={o.path}>{o.path}</span>
                      {o.summary && <span className="s">{o.summary}</span>}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
          {selected ? <Operation key={selected.method + selected.path} spec={data!.openapi} op={selected} /> : <div className="card empty">Choose an API on the left to call it.</div>}
        </div>
      )}
    </>
  );
}

function Operation({ spec, op }: { spec: Spec; op: Op }) {
  const params = (op.op.parameters || []).map((p: any) => (p.$ref ? resolve(spec, p) : p)).filter((p: any) => p.in !== "header" || !GATEWAY_HEADERS.has(String(p.name).toLowerCase()));
  const [values, setValues] = useState<Record<string, string>>({});
  const [body, setBody] = useState<unknown>(undefined);
  const [bodyError, setBodyError] = useState<string | null>(null);
  const [result, setResult] = useState<ApiResult | null>(null);
  const [busy, setBusy] = useState(false);
  const bodySchema = op.op.requestBody?.content?.["application/json"]?.schema;
  const destructive = op.method !== "GET";

  const url = useMemo(() => {
    let p = op.path;
    const query = new URLSearchParams();
    for (const prm of params) {
      const v = values[prm.name];
      if (prm.in === "path") p = p.replace(`{${prm.name}}`, encodeURIComponent(v || `{${prm.name}}`));
      else if (prm.in === "query" && v !== undefined && v !== "") query.set(prm.name, v);
    }
    const q = query.toString();
    return q ? `${p}?${q}` : p;
  }, [op.path, params, values]);
  const missing = params.filter((p: any) => p.required && !values[p.name]).map((p: any) => p.name);

  async function run() {
    if (destructive && !confirm(`${op.method} ${url}\n\nThis changes live data. Continue?`)) return;
    setBusy(true);
    try {
      setResult(await api(op.method, url, bodySchema ? body : undefined));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid">
      <div className="card">
        <div className="row mb"><span className={`method m-${op.method}`}>{op.method}</span><span className="mono">{op.path}</span></div>
        {op.summary && <h3>{op.summary}</h3>}
        {op.op.description && <div className="sub" style={{ whiteSpace: "pre-wrap" }}>{op.op.description}</div>}
        {params.length > 0 && (
          <div className="form mt">
            {params.map((p: any) => {
              const s = resolve(spec, p.schema || {});
              return (
                <label key={p.in + p.name} className="field">
                  <span>{p.name}{p.required && <span className="req"> *</span>} <span className="muted" style={{ fontSize: 11 }}>{p.in} · {s.format || s.type || ""}</span></span>
                  {s.enum ? (
                    <select value={values[p.name] ?? ""} onChange={(e) => setValues((x) => ({ ...x, [p.name]: e.target.value }))}>
                      <option value="">(not set)</option>{s.enum.map((o: any) => <option key={o}>{o}</option>)}
                    </select>
                  ) : (
                    <input placeholder={s.default !== undefined ? `default ${s.default}` : ""} value={values[p.name] ?? ""} onChange={(e) => setValues((x) => ({ ...x, [p.name]: e.target.value }))} />
                  )}
                </label>
              );
            })}
          </div>
        )}
        {bodySchema && (
          <div className="mt">
            <div className="muted mb" style={{ fontSize: 12 }}>Request body</div>
            <SchemaForm spec={spec} schema={bodySchema} onBody={(b, e) => { setBody(b); setBodyError(e); }} />
          </div>
        )}
        {bodyError && <div className="notice bad mt">{bodyError}</div>}
        <div className="row mt">
          <button disabled={busy || missing.length > 0 || !!bodyError} className={destructive ? "danger" : ""} onClick={run}>{busy ? "Calling…" : `Send ${op.method}`}</button>
          <span className="mono muted">{url}</span>
          {missing.length > 0 && <span className="muted">needs: {missing.join(", ")}</span>}
        </div>
      </div>
      {result && (
        <div className="card">
          <div className="row mb">
            <Badge kind={result.ok ? "ok" : "bad"}>HTTP {result.status}</Badge>
            <span className="muted">{result.ms} ms</span>
            {Array.isArray(result.data) && <span className="muted">{result.data.length} rows</span>}
          </div>
          {Array.isArray(result.data) && result.data.length && typeof result.data[0] === "object" ? <AutoTable rows={result.data} /> : <JsonView value={result.data} />}
        </div>
      )}
    </div>
  );
}
