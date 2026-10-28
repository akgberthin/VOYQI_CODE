"use client";

import { useState } from "react";
import { del, get, must, post, put } from "@/lib/api";
import { Badge, ErrorNote, Loading, PageHead, fmtTime, useAction, useLoad } from "@/lib/ui";

type Field = { key: string; label: string; secret?: boolean; required?: boolean; hint?: string; default?: string; choices?: string[]; multiline?: boolean };
type Integration = {
  provider: string; label: string; category: string; used_for: string; fields: Field[]; configured: boolean; enabled: boolean;
  values: Record<string, any>; test_status: string; test_detail: string; tested_at: string | null; updated_by: string;
};

const STATUS: Record<string, [string, string]> = { ok: ["ok", "connected"], failed: ["bad", "test failed"], untested: ["warn", "not tested"] };

export default function Integrations() {
  const { data, error, loading, reload } = useLoad(() => must<Integration[]>(get("/api/v1/rbac/integrations")));
  const [open, setOpen] = useState<string | null>(null);
  const categories = Array.from(new Set((data || []).map((i) => i.category)));
  return (
    <>
      <PageHead title="Integrations" text="Credentials for the outside services the platform uses — AI, email, SMS and voice, push, social networks, payments. They're encrypted at rest and never shown again after saving; each can be tested against the provider. Changes reach the services within a minute." />
      <ErrorNote error={error} />
      {loading && !data ? <Loading /> : categories.map((cat) => (
        <div key={cat} className="mb">
          <h3>{cat}</h3>
          <div className="grid cols-2 mt">
            {(data || []).filter((i) => i.category === cat).map((i) => (
              <Card key={i.provider} it={i} open={open === i.provider} onToggle={() => setOpen(open === i.provider ? null : i.provider)} onChange={reload} />
            ))}
          </div>
        </div>
      ))}
    </>
  );
}

function Card({ it, open, onToggle, onChange }: { it: Integration; open: boolean; onToggle: () => void; onChange: () => void }) {
  const run = useAction();
  const [values, setValues] = useState<Record<string, string>>({});
  const [enabled, setEnabled] = useState(it.enabled || !it.configured);
  const [testing, setTesting] = useState(false);
  const [kind, label] = it.configured ? STATUS[it.test_status] || ["", it.test_status] : ["", "not set up"];
  const value = (f: Field) => values[f.key] ?? (f.secret ? "" : String(it.values[f.key] ?? f.default ?? ""));
  const save = () => run(`${it.label} saved`, () => must(put(`/api/v1/rbac/integrations/${it.provider}`, { values, enabled }))).then((ok) => { if (ok) { setValues({}); onChange(); } });
  const test = async () => {
    setTesting(true);
    await run(`${it.label} tested`, () => must(post(`/api/v1/rbac/integrations/${it.provider}/test`)));
    setTesting(false);
    onChange();
  };
  return (
    <div className="card" style={open ? { borderColor: "var(--gold)" } : undefined}>
      <div className="row between">
        <div><h3>{it.label}</h3><div className="sub">{it.used_for}</div></div>
        <div className="row">{it.configured && !it.enabled && <Badge>disabled</Badge>}<Badge kind={kind}>{label}</Badge></div>
      </div>
      {it.configured && it.test_detail && <div className={`notice ${it.test_status === "ok" ? "ok" : "bad"} mt`}>{it.test_detail}<span className="muted"> · {fmtTime(it.tested_at)}</span></div>}
      <div className="row mt">
        <button className="sm ghost" onClick={onToggle}>{open ? "Close" : it.configured ? "Edit" : "Set up"}</button>
        {it.configured && <button className="sm ghost" disabled={testing} onClick={test}>{testing ? "Testing…" : "Test connection"}</button>}
        {it.configured && <button className="sm ghost" onClick={() => confirm(`Remove the ${it.label} credentials? Anything using them stops working.`) && run("Removed", () => must(del(`/api/v1/rbac/integrations/${it.provider}`))).then(onChange)}>Remove</button>}
      </div>
      {open && (
        <div className="mt">
          <div className="form">
            {it.fields.map((f) => {
              const stored = f.secret ? it.values[f.key] : null;
              const placeholder = f.secret ? (stored?.set ? `stored (${stored.preview}) — leave blank to keep` : f.hint || "") : f.hint || "";
              const set = (v: string) => setValues((x) => ({ ...x, [f.key]: v }));
              return (
                <label key={f.key} className="field" style={f.multiline ? { gridColumn: "1 / -1" } : undefined}>
                  <span>{f.label}{f.required && <span className="req"> *</span>}{f.secret && <span className="muted"> · secret</span>}</span>
                  {f.choices ? (
                    <select value={value(f)} onChange={(e) => set(e.target.value)}>{f.choices.map((c) => <option key={c}>{c}</option>)}</select>
                  ) : f.multiline ? (
                    <textarea placeholder={placeholder} value={value(f)} onChange={(e) => set(e.target.value)} style={{ minHeight: 100 }} />
                  ) : (
                    <input type={f.secret ? "password" : "text"} autoComplete="off" placeholder={placeholder} value={value(f)} onChange={(e) => set(e.target.value)} />
                  )}
                </label>
              );
            })}
          </div>
          <div className="row mt">
            <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled</label>
            <button onClick={save}>Save</button>
          </div>
          {it.updated_by && <div className="muted mt" style={{ fontSize: 12 }}>Last changed by {it.updated_by}</div>}
        </div>
      )}
    </div>
  );
}
