"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

/** Load data on mount (and when deps change); `reload` re-runs it. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, deps);
  return { data, error, loading, reload, setData };
}

type Toast = { id: number; kind: "ok" | "bad" | "info"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "bad" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast">{toasts.map((t) => <div key={t.id} className={`notice ${t.kind}`}>{t.text}</div>)}</div>
    </ToastCtx.Provider>
  );
}

/** Runs an action with a toast for its outcome; returns whether it worked. */
export function useAction() {
  const toast = useToast();
  return useCallback(async (label: string, fn: () => Promise<unknown>): Promise<boolean> => {
    try {
      await fn();
      toast("ok", label);
      return true;
    } catch (e: any) {
      toast("bad", e?.message || String(e));
      return false;
    }
  }, [toast]);
}

export function PageHead({ title, text, children }: { title: string; text?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div><h1>{title}</h1>{text && <p>{text}</p>}</div>
      {children && <div className="row">{children}</div>}
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="empty"><span className="spin" /> {what}…</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="notice bad mb">{error}</div> : null;
}

export function Badge({ kind = "", children }: { kind?: string; children: ReactNode }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

const STATUS_KIND: Record<string, string> = {
  active: "ok", approved: "ok", completed: "ok", success: "ok", healthy: "ok", published: "ok", applied: "ok", passed: "ok",
  paused: "warn", pending: "warn", draft: "warn", proposed: "warn", scored: "warn", open: "info", evaluated: "info",
  disabled: "bad", rejected: "bad", revoked: "bad", suspended: "bad", ended: "", retired: "", declined: "bad", failed: "bad", expired: "",
};
export function StatusBadge({ value }: { value: unknown }) {
  const v = String(value ?? "");
  return <Badge kind={STATUS_KIND[v.toLowerCase()] ?? ""}>{v.replace(/_/g, " ") || "—"}</Badge>;
}

export function JsonView({ value }: { value: unknown }) {
  return <pre className="json">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

function cell(v: unknown): ReactNode {
  if (v === null || v === undefined) return <span className="muted">—</span>;
  if (typeof v === "boolean") return v ? <Badge kind="ok">yes</Badge> : <Badge>no</Badge>;
  if (typeof v === "object") return <span className="mono clip" title={JSON.stringify(v)}>{JSON.stringify(v)}</span>;
  const s = String(v);
  if (/^\d{4}-\d\d-\d\dT/.test(s)) return <span className="muted">{new Date(s).toLocaleString()}</span>;
  return <span className="clip" title={s}>{s}</span>;
}

/** Any list of objects as a table — used for API responses of unknown shape. */
export function AutoTable({ rows, max = 200 }: { rows: any[]; max?: number }) {
  if (!rows.length) return <div className="empty">No rows.</div>;
  const cols = Array.from(rows.slice(0, 50).reduce((s: Set<string>, r) => { Object.keys(r ?? {}).forEach((k) => s.add(k)); return s; }, new Set<string>()));
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{rows.slice(0, max).map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{cell(r?.[c])}</td>)}</tr>)}</tbody>
      </table>
      {rows.length > max && <div className="muted mt">Showing {max} of {rows.length}.</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export const fmtTime = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
export const pretty = (s?: string | null) => (s || "").replace(/_/g, " ");
