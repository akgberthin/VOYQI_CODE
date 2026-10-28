"use client";

import { useState } from "react";
import { get, must, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "@/lib/ui";

export function Sessions() {
  const run = useAction();
  const { me } = useSession();
  const bg = useLoad(() => must<any[]>(get("/api/v1/identity/break-glass")));
  const imp = useLoad(() => must<any[]>(get("/api/v1/identity/impersonation")));
  const [form, setForm] = useState({ user_id: "", reason: "", duration_minutes: "30" });
  const end = (kind: string, id: string, reload: () => void) => {
    const reason = prompt("Reason for ending this session") || "ended by super admin";
    run("Session ended", () => must(post(`/api/v1/identity/${kind}/${id}/end`, { ended_by: me?.email || "super.admin", reason }))).then(reload);
  };
  const table = (rows: any[] | null, kind: string, reload: () => void, who: (r: any) => string) => (
    !rows?.length ? <Empty>None.</Empty> : (
      <table><thead><tr><th>Who</th><th>Reason</th><th>Started</th><th>Off-hours</th><th>Status</th><th /></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.id}><td>{who(r)}</td><td>{r.reason}</td><td className="muted">{fmtTime(r.started_at)}</td><td>{r.off_hours ? "yes" : "no"}</td>
            <td><StatusBadge value={r.status} />{r.ended_by && <div className="muted">by {r.ended_by}</div>}</td>
            <td>{r.status === "active" && <button className="sm danger" onClick={() => end(kind, r.id, reload)}>End now</button>}</td></tr>
        ))}</tbody></table>
    )
  );
  return (
    <>
      <PageHead title="Privileged sessions" text="Emergency (break-glass) access and impersonation. Each activation is reviewed by the Compliance Agent; any active session can be ended here immediately." />
      <ErrorNote error={bg.error || imp.error} />
      <div className="card mb">
        <h3>Activate break-glass</h3>
        <div className="form mt">
          <label className="field">Account<input placeholder="user id or email" value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} /></label>
          <label className="field">Reason<input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></label>
          <label className="field">Minutes<input type="number" value={form.duration_minutes} onChange={(e) => setForm({ ...form, duration_minutes: e.target.value })} /></label>
        </div>
        <button className="mt danger" disabled={!form.user_id || !form.reason} onClick={() => run("Break-glass active — Compliance Agent notified", () => must(post("/api/v1/identity/break-glass", { ...form, duration_minutes: Number(form.duration_minutes), activated_by: me?.email || "super.admin" }))).then(bg.reload)}>Activate</button>
      </div>
      <h3>Break-glass</h3>
      <div className="card mb table-wrap">{bg.loading ? <Loading /> : table(bg.data, "break-glass", bg.reload, (r) => `${r.activated_by} → ${r.user_id}`)}</div>
      <h3>Impersonation</h3>
      <div className="card table-wrap">{imp.loading ? <Loading /> : table(imp.data, "impersonation", imp.reload, (r) => `${r.admin_user_id} as ${r.target_user_id}`)}</div>
    </>
  );
}

export function AuditLog() {
  const [actor, setActor] = useState("");
  const [q, setQ] = useState("");
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get(`/api/v1/rbac/audit-log${actor ? `?actor_type=${actor}` : ""}`)), [actor]);
  const rows = (data || []).filter((e) => !q || `${e.actor_id} ${e.action} ${e.resource} ${e.detail}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <>
      <PageHead title="Platform audit log" text="Every human and AI action on access control, tenants and platform configuration, newest first. AI reasoning has its own trail under AI → Decision log.">
        <input placeholder="Search actor, action, resource…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={actor} onChange={(e) => setActor(e.target.value)}><option value="">All actors</option><option value="human">Human only</option><option value="ai">AI only</option></select>
        <button className="ghost" onClick={reload}>Refresh</button>
      </PageHead>
      <ErrorNote error={error} />
      {loading ? <Loading /> : !rows.length ? <Empty>No entries.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Resource</th><th>Detail</th></tr></thead>
          <tbody>{rows.slice(0, 500).map((e) => (
            <tr key={e.id}>
              <td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.occurred_at)}</td>
              <td><span className={`badge ${e.actor_type === "ai" ? "ai" : ""}`}>{e.actor_type}</span> {e.actor_id}</td>
              <td className="mono">{e.action}</td>
              <td className="mono clip" title={e.resource}>{e.resource}</td>
              <td className="muted clip" title={e.detail}>{e.detail || "—"}</td>
            </tr>
          ))}</tbody>
        </table>{rows.length > 500 && <div className="muted mt">Showing 500 of {rows.length}.</div>}</div>
      )}
    </>
  );
}
