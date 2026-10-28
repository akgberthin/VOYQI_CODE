"use client";

/* Builds a form from an OpenAPI request-body schema, so any endpoint on
   the platform can be called without hand-writing its screen. Fields it
   can't render simply (nested objects, arrays of objects) take JSON. */
import { useEffect, useState } from "react";

export type Spec = { components?: { schemas?: Record<string, any> } };

export function resolve(spec: Spec, schema: any, depth = 0): any {
  if (!schema || depth > 8) return schema || {};
  if (schema.$ref) {
    const name = String(schema.$ref).split("/").pop()!;
    return resolve(spec, spec.components?.schemas?.[name], depth + 1);
  }
  if (schema.allOf?.length === 1) return resolve(spec, { ...schema, ...schema.allOf[0], allOf: undefined }, depth + 1);
  const variants = schema.anyOf || schema.oneOf;
  if (variants) {
    const real = variants.filter((v: any) => v.type !== "null");
    if (real.length === 1) return { ...resolve(spec, real[0], depth + 1), nullable: true, default: schema.default, title: schema.title, description: schema.description };
  }
  return schema;
}

type Kind = "string" | "number" | "boolean" | "enum" | "list" | "json";

function kindOf(s: any): Kind {
  if (s.enum) return "enum";
  if (s.type === "boolean") return "boolean";
  if (s.type === "integer" || s.type === "number") return "number";
  if (s.type === "array" && ["string", "integer", "number"].includes(s.items?.type)) return "list";
  if (s.type === "string" || (!s.type && !s.properties)) return "string";
  return "json";
}

export function exampleFor(spec: Spec, schema: any, depth = 0): any {
  const s = resolve(spec, schema);
  if (s.default !== undefined) return s.default;
  if (s.example !== undefined) return s.example;
  if (s.enum) return s.enum[0];
  if (depth > 4) return null;
  switch (s.type) {
    case "object": return Object.fromEntries(Object.entries(s.properties || {}).filter(([k]) => (s.required || []).includes(k)).map(([k, v]) => [k, exampleFor(spec, v, depth + 1)]));
    case "array": return [];
    case "integer": case "number": return 0;
    case "boolean": return false;
    case "string": return s.format === "uuid" ? "" : "";
    default: return s.properties ? exampleFor(spec, { ...s, type: "object" }, depth) : null;
  }
}

/** Form values (strings from inputs) → the JSON body the API expects. */
function toBody(spec: Spec, schema: any, values: Record<string, string>): any {
  const s = resolve(spec, schema);
  const out: Record<string, any> = {};
  for (const [key, raw] of Object.entries(s.properties || {})) {
    const prop = resolve(spec, raw);
    const v = values[key];
    if (v === undefined || v === "") continue;
    switch (kindOf(prop)) {
      case "number": out[key] = Number(v); break;
      case "boolean": out[key] = v === "true"; break;
      case "list": out[key] = v.split(",").map((x) => x.trim()).filter(Boolean).map((x) => (prop.items?.type === "string" ? x : Number(x))); break;
      case "json": out[key] = JSON.parse(v); break;
      default: out[key] = v;
    }
  }
  return out;
}

export function SchemaForm({ spec, schema, onBody }: { spec: Spec; schema: any; onBody: (body: unknown, error: string | null) => void }) {
  const s = resolve(spec, schema);
  const isObject = s.type === "object" || !!s.properties;
  const [raw, setRaw] = useState(!isObject);
  const [values, setValues] = useState<Record<string, string>>({});
  const [text, setText] = useState(() => JSON.stringify(exampleFor(spec, schema), null, 2));

  useEffect(() => {
    try {
      if (raw) onBody(text.trim() ? JSON.parse(text) : undefined, null);
      else onBody(toBody(spec, schema, values), null);
    } catch (e: any) {
      onBody(undefined, `Invalid JSON: ${e.message}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raw, text, values]);

  const required = new Set<string>(s.required || []);
  return (
    <div>
      {isObject && (
        <div className="row mb">
          <button className={`sm ${raw ? "ghost" : ""}`} onClick={() => setRaw(false)}>Form</button>
          <button className={`sm ${raw ? "" : "ghost"}`} onClick={() => {
            try { setText(JSON.stringify({ ...exampleFor(spec, schema), ...toBody(spec, schema, values) }, null, 2)); } catch { /* keep text */ }
            setRaw(true);
          }}>JSON</button>
        </div>
      )}
      {raw ? (
        <textarea value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      ) : (
        <div className="form">
          {Object.entries(s.properties || {}).map(([key, rawProp]) => {
            const prop = resolve(spec, rawProp);
            const kind = kindOf(prop);
            const set = (v: string) => setValues((x) => ({ ...x, [key]: v }));
            const hint = [prop.type === "array" ? `list of ${prop.items?.type || "values"}` : prop.format || prop.type, prop.default !== undefined ? `default ${JSON.stringify(prop.default)}` : ""].filter(Boolean).join(" · ");
            return (
              <label key={key} className="field" title={prop.description || ""} style={kind === "json" ? { gridColumn: "1 / -1" } : undefined}>
                <span>{key}{required.has(key) && <span className="req"> *</span>} <span className="muted" style={{ fontSize: 11 }}>{hint}</span></span>
                {kind === "enum" ? (
                  <select value={values[key] ?? ""} onChange={(e) => set(e.target.value)}>
                    <option value="">{required.has(key) ? "— choose —" : "(not set)"}</option>
                    {prop.enum.map((o: any) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
                  </select>
                ) : kind === "boolean" ? (
                  <select value={values[key] ?? ""} onChange={(e) => set(e.target.value)}>
                    <option value="">(not set)</option><option value="true">true</option><option value="false">false</option>
                  </select>
                ) : kind === "json" ? (
                  <textarea style={{ minHeight: 70 }} placeholder={JSON.stringify(exampleFor(spec, prop))} value={values[key] ?? ""} onChange={(e) => set(e.target.value)} />
                ) : (
                  <input type={kind === "number" ? "number" : "text"} placeholder={kind === "list" ? "comma, separated" : prop.format === "uuid" ? "uuid" : ""}
                    value={values[key] ?? ""} onChange={(e) => set(e.target.value)} />
                )}
              </label>
            );
          })}
          {!Object.keys(s.properties || {}).length && <div className="muted">This request takes an empty body.</div>}
        </div>
      )}
    </div>
  );
}
