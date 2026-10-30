"use client";

import { useState } from "react";
import { api, get, must, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Badge, Empty, ErrorNote, Loading, PageHead, StatusBadge, fmtTime, useAction, useLoad } from "@/lib/ui";
import { CONSOLES, PERMISSION_KEYS, allows } from "../permissions";

// ── Users & their roles ───────────────────────────────────────────────
const DAY = 86400e3;
/** Most recent of: last request through the gateway, last sign-in. */
const lastSeen = (u: any): string | null => [u.last_seen_at, u.last_login_at].filter(Boolean).sort().pop() || null;
const ago = (iso: string | null) => {
  if (!iso) return "never";
  const d = Date.now() - new Date(iso).getTime();
  if (d < 3600e3) return `${Math.max(1, Math.round(d / 60e3))}m ago`;
  if (d < DAY) return `${Math.round(d / 3600e3)}h ago`;
  return `${Math.round(d / DAY)}d ago`;
};
const mfaOn = (u: any) => u.login_mfa_enabled ?? u.mfa_enabled;
export function Users() {
  const run = useAction();
  const { me } = useSession();
  const users = useLoad(() => must<any[]>(get("/api/v1/rbac/users")));
  const roles = useLoad(() => must<any[]>(get("/api/v1/rbac/roles")));
  const [sel, setSel] = useState<any>(null);
  const detail = useLoad(async () => {
    if (!sel) return null;
    const [ctx, eff] = await Promise.all([
      must<any>(get(`/api/v1/rbac/users/${sel.id}/context`)),
      must<any>(get(`/api/v1/rbac/users/${sel.id}/effective-permissions`)),
    ]);
    return { ctx, eff };
  }, [sel?.id]);
  const [roleId, setRoleId] = useState("");
  const [ovr, setOvr] = useState({ permission_key: "", effect: "allow", reason: "" });
  const [add, setAdd] = useState({ email: "", display_name: "", id: "", mfa_enabled: false });
  const [adding, setAdding] = useState(false);
  const actor = me?.email || "super.admin";
  const all = users.data || [];
  const [filter, setFilter] = useState("");
  const shown = all.filter((u) => !filter || (filter === "locked" ? u.locked : filter === "inactive" ? !lastSeen(u) || Date.now() - new Date(lastSeen(u)!).getTime() > 30 * DAY : filter === "suspended" ? u.status === "suspended" : !mfaOn(u)));
  const statusError = all.find((u) => u.account_status_error)?.account_status_error;
  const unlock = (u: any) => run(`${u.display_name} unlocked — they can sign in again`, () => must(post(`/api/v1/rbac/users/${u.id}/unlock`, { unlocked_by: actor }))).then(users.reload);
  const [edit, setEdit] = useState<any>(null);
  const saveEdit = () => run("User updated", () => must(api("PATCH", `/api/v1/rbac/users/${sel.id}`, { display_name: edit.display_name, email: edit.email, mfa_enabled: edit.mfa_enabled, updated_by: actor })))
    .then((ok) => { if (ok) { setSel({ ...sel, ...edit }); setEdit(null); users.reload(); } });
  const setStatus = (status: "active" | "suspended") => {
    const reason = status === "suspended" ? prompt(`Suspend ${sel.display_name}? They keep their roles on record but lose all access. Reason:`) : "";
    if (reason === null) return;
    run(status === "suspended" ? "Suspended — access removed" : "Reactivated", () => must(api("PATCH", `/api/v1/rbac/users/${sel.id}`, { status, updated_by: actor, reason: reason || "" })))
      .then((ok) => { if (ok) { setSel({ ...sel, status }); users.reload(); detail.reload(); } });
  };
  const createUser = () => run("User created", () => must(post("/api/v1/rbac/users", { email: add.email, display_name: add.display_name, mfa_enabled: add.mfa_enabled, ...(add.id ? { id: add.id } : {}) })))
    .then((ok) => { if (ok) { setAdd({ email: "", display_name: "", id: "", mfa_enabled: false }); setAdding(false); users.reload(); } });

  return (
    <>
      <PageHead title="Users & roles" text="Every platform user in this tenant. Pick one to see their roles, their effective permissions (deny always wins) and any overrides — and change them.">
        <button onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add user"}</button>
      </PageHead>
      {adding && (
        <div className="card mb">
          <h3>Add platform user</h3>
          <div className="sub">The gateway grants permissions by the user id in someone&apos;s login token, so use the same id as their account in user-svc (leave blank only for a user who will never sign in).</div>
          <div className="form mt">
            <label className="field">Email<input value={add.email} onChange={(e) => setAdd({ ...add, email: e.target.value })} /></label>
            <label className="field">Display name<input value={add.display_name} onChange={(e) => setAdd({ ...add, display_name: e.target.value })} /></label>
            <label className="field">User id (from user-svc)<input className="mono" placeholder="uuid" value={add.id} onChange={(e) => setAdd({ ...add, id: e.target.value.trim() })} /></label>
            <label className="field">MFA<select value={String(add.mfa_enabled)} onChange={(e) => setAdd({ ...add, mfa_enabled: e.target.value === "true" })}><option value="false">off</option><option value="true">on</option></select></label>
          </div>
          <button className="mt" disabled={!add.email || !add.display_name} onClick={createUser}>Create user</button>
        </div>
      )}
      <ErrorNote error={users.error} />
      <div className="grid cols-4 mb">
        {([["", "Total users", all.length, `${all.filter((u) => u.status === "suspended").length} suspended`],
          ["nomfa", "MFA enabled", `${all.filter(mfaOn).length}/${all.length}`, `${all.filter((u) => !mfaOn(u)).length} without — click to list`],
          ["inactive", "Inactive 30d", all.filter((u) => !lastSeen(u) || Date.now() - new Date(lastSeen(u)!).getTime() > 30 * DAY).length, "review required"],
          ["locked", "Locked", statusError ? "—" : all.filter((u) => u.locked).length, "failed sign-in attempts"]] as const).map(([k, l, n, d]) => (
          <button key={l} className="card kpi" style={{ textAlign: "left", color: "inherit", borderColor: filter === k && k ? "var(--gold)" : undefined }} onClick={() => setFilter(filter === k ? "" : k)}>
            <div className="l">{l}</div><div className="n" style={k === "locked" && Number(n) > 0 ? { color: "var(--bad)" } : undefined}>{n}</div><div className="d">{d}</div>
          </button>
        ))}
      </div>
      {statusError && <div className="notice warn mb">Sign-in status (locked accounts, last login) is unavailable: {statusError}</div>}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.25fr) minmax(0,1fr)" }}>
        <div className="card table-wrap">
          {users.loading ? <Loading /> : (
            <table>
              <thead><tr><th>User</th><th>Roles</th><th>MFA</th><th>Last seen</th><th>Status</th><th /></tr></thead>
              <tbody>{shown.map((u) => (
                <tr key={u.id} className="click" onClick={() => { setSel(u); setEdit(null); }} style={sel?.id === u.id ? { outline: "1px solid var(--gold)" } : undefined}>
                  <td><b>{u.display_name}</b><div className="muted">{u.email}</div></td>
                  <td>{u.role_keys?.length ? u.role_keys.map((k: string) => <span key={k} className="badge" style={{ fontSize: 10 }}>{k}</span>) : <span className="muted">—</span>}</td>
                  <td>{mfaOn(u) ? <Badge kind="ok">on</Badge> : <Badge kind="bad">off</Badge>}</td>
                  <td className="muted" title={lastSeen(u) ? fmtTime(lastSeen(u)!) : ""}>{ago(lastSeen(u))}{u.has_login === false && <div style={{ fontSize: 11 }}>no sign-in account</div>}</td>
                  <td>{u.locked ? <Badge kind="bad">locked</Badge> : <StatusBadge value={u.status} />}</td>
                  <td>{u.locked && <button className="sm danger" title={`${u.failed_login_attempts} failed attempts; locked until ${fmtTime(u.locked_until)}`} onClick={(e) => { e.stopPropagation(); unlock(u); }}>Unlock</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>
        <div className="card">
          {!sel ? <Empty>Select a user.</Empty> : detail.loading && !detail.data ? <Loading /> : detail.data && (
            <>
              <div className="row between"><h3>{sel.display_name}</h3><div className="row">
                <button className="sm ghost" onClick={() => setEdit(edit ? null : { display_name: sel.display_name, email: sel.email, mfa_enabled: sel.mfa_enabled })}>{edit ? "Cancel" : "Edit"}</button>
                {sel.status === "suspended" ? <button className="sm" onClick={() => setStatus("active")}>Reactivate</button> : <button className="sm danger" onClick={() => setStatus("suspended")}>Suspend</button>}
              </div></div>
              <div className="sub mono">{sel.id}</div>
              {sel.status === "suspended" && <div className="notice warn mt">Suspended — the gateway refuses all of this user&apos;s requests. Their roles stay on record for reactivation.</div>}
              {edit && (
                <div className="mt">
                  <div className="form">
                    <label className="field">Display name<input value={edit.display_name} onChange={(e) => setEdit({ ...edit, display_name: e.target.value })} /></label>
                    <label className="field">Email<input value={edit.email} onChange={(e) => setEdit({ ...edit, email: e.target.value })} /></label>
                    <label className="field">MFA required<select value={String(edit.mfa_enabled)} onChange={(e) => setEdit({ ...edit, mfa_enabled: e.target.value === "true" })}><option value="true">yes</option><option value="false">no</option></select></label>
                  </div>
                  <div className="sub mt">Sign-in email and the user&apos;s own MFA enrolment live in their account (auth-svc); this edits the platform record.</div>
                  <button className="mt" disabled={!edit.display_name.trim() || !edit.email.trim()} onClick={saveEdit}>Save</button>
                </div>
              )}
              <h4 className="mt">Roles</h4>
              <div className="row">
                {detail.data.ctx.assignments.map((a: any) => (
                  <span key={a.id} className="badge gold">{a.role_key}
                    <button className="sm ghost" style={{ padding: "0 6px", marginLeft: 4 }} title="Revoke"
                      onClick={() => run(`Revoked ${a.role_key}`, () => must(post(`/api/v1/rbac/role-assignments/${a.id}/revoke`, { revoked_by: actor, reason: "super admin" }))).then(detail.reload)}>×</button>
                  </span>
                ))}
                {!detail.data.ctx.assignments.length && <span className="muted">No roles.</span>}
              </div>
              <div className="row mt">
                <select value={roleId} onChange={(e) => setRoleId(e.target.value)}><option value="">Assign a role…</option>{(roles.data || []).map((r) => <option key={r.id} value={r.id}>{r.name} ({r.key})</option>)}</select>
                <button disabled={!roleId} onClick={() => run("Role assigned", () => must(post("/api/v1/rbac/role-assignments", { user_id: sel.id, role_id: roleId, assigned_by: actor }))).then(() => { setRoleId(""); detail.reload(); })}>Assign</button>
              </div>
              <h4 className="mt">Effective permissions</h4>
              <div className="row">{detail.data.eff.effective_permissions.map((p: string) => <Badge key={p} kind={p.includes("*") ? "gold" : "info"}>{p}</Badge>)}{detail.data.eff.denied.map((p: string) => <Badge key={p} kind="bad">✕ {p}</Badge>)}</div>
              <h4 className="mt">Overrides</h4>
              {detail.data.ctx.active_overrides.length ? (
                <table><tbody>{detail.data.ctx.active_overrides.map((o: any) => (
                  <tr key={o.id}><td><Badge kind={o.effect === "deny" ? "bad" : "ok"}>{o.effect}</Badge> <span className="mono">{o.permission_key}</span>{o.is_sensitive && <Badge kind="warn">sensitive</Badge>}</td><td className="muted">{o.reason}</td>
                    <td><button className="sm ghost" onClick={() => run("Override revoked", () => must(post(`/api/v1/rbac/permission-overrides/${o.id}/revoke`, { revoked_by: actor, reason: "super admin" }))).then(detail.reload)}>Revoke</button></td></tr>
                ))}</tbody></table>
              ) : <div className="muted">None.</div>}
              <div className="form mt">
                <label className="field">Permission<input placeholder="fleet.write" value={ovr.permission_key} onChange={(e) => setOvr({ ...ovr, permission_key: e.target.value })} /></label>
                <label className="field">Effect<select value={ovr.effect} onChange={(e) => setOvr({ ...ovr, effect: e.target.value })}><option>allow</option><option>deny</option></select></label>
                <label className="field">Reason<input value={ovr.reason} onChange={(e) => setOvr({ ...ovr, reason: e.target.value })} /></label>
              </div>
              <button className="mt" disabled={!ovr.permission_key} onClick={() => run("Override added", () => must(post("/api/v1/rbac/permission-overrides", { ...ovr, user_id: sel.id, granted_by: actor }))).then(() => { setOvr({ permission_key: "", effect: "allow", reason: "" }); detail.reload(); })}>Add override</button>
            </>
          )}
        </div>
      </div>
    </>
  );
}

// ── Roles ─────────────────────────────────────────────────────────────
const rolePerms = (r: any): string[] => [...(r.permission_keys || []), ...(r.wildcard_permissions || [])];

export function Roles() {
  const run = useAction();
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get("/api/v1/rbac/roles")));
  const { me } = useSession();
  const assignments = useLoad(() => must<any[]>(get("/api/v1/rbac/role-assignments")));
  const people = useLoad(() => must<any[]>(get("/api/v1/rbac/users")));
  const who = (id: string) => (people.data || []).find((u) => u.id === id);
  const holders = (roleId: string) => (assignments.data || []).filter((a) => a.role_id === roleId);
  const [roleFilter, setRoleFilter] = useState("");
  const [form, setForm] = useState({ key: "", name: "", perms: [] as string[] });
  const [pick, setPick] = useState("");
  const [check, setCheck] = useState({ role: "", permission: "" });
  const addPerm = (p: string) => { const k = p.trim(); if (k && !form.perms.includes(k)) setForm({ ...form, perms: [...form.perms, k] }); setPick(""); };
  const testRole = (data || []).find((r) => r.key === check.role);
  const verdict = testRole && check.permission ? allows(rolePerms(testRole), check.permission.trim()) : null;
  return (
    <>
      <PageHead title="Roles" text="Bundles of permissions. Permissions are <module>.read / .write / .* (or * for everything); the gateway checks them on every request. Assigning a defined role to a user is routine; changing what a role inherits goes through an ABAC policy." />
      <ErrorNote error={error} />
      <div className="split mb">
        <div className="card">
          <h3>Define role</h3>
          <div className="form mt">
            <label className="field">Key<input placeholder="ops_lead" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} /></label>
            <label className="field">Name<input placeholder="Operations lead" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          </div>
          <div className="row mt">
            <input list="perm-keys" style={{ flex: 1 }} placeholder="Add a permission — fleet.read, rental.*, …" value={pick} onChange={(e) => setPick(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addPerm(pick)} />
            <datalist id="perm-keys">{PERMISSION_KEYS.map((p) => <option key={p} value={p} />)}</datalist>
            <button className="ghost" disabled={!pick.trim()} onClick={() => addPerm(pick)}>Add</button>
          </div>
          <div className="row mt">{form.perms.length ? form.perms.map((p) => (
            <span key={p} className={`badge ${p.includes("*") ? "gold" : "info"}`}>{p}<button className="sm ghost" style={{ padding: "0 5px", marginLeft: 3 }} onClick={() => setForm({ ...form, perms: form.perms.filter((x) => x !== p) })}>×</button></span>
          )) : <span className="muted">No permissions yet.</span>}</div>
          <button className="mt" disabled={!form.key || !form.name} onClick={() => run("Role created", () => must(post("/api/v1/rbac/roles", { key: form.key, name: form.name, permission_keys: form.perms }))).then((ok) => { if (ok) { setForm({ key: "", name: "", perms: [] }); reload(); } })}>Create role</button>
        </div>
        <div className="card">
          <h3>Permission checker</h3>
          <div className="sub">Would this role pass the gateway for this permission? Same rule as the gateway: exact key, <span className="mono">module.*</span> or <span className="mono">*</span>.</div>
          <div className="form mt">
            <label className="field">Role<select value={check.role} onChange={(e) => setCheck({ ...check, role: e.target.value })}><option value="">— choose —</option>{(data || []).map((r) => <option key={r.id} value={r.key}>{r.name}</option>)}</select></label>
            <label className="field">Permission<input list="perm-keys" placeholder="fleet.write" value={check.permission} onChange={(e) => setCheck({ ...check, permission: e.target.value })} /></label>
          </div>
          {verdict !== null && <div className={`notice ${verdict ? "ok" : "bad"} mt`}>{verdict ? "Allowed" : "Refused (403)"} — {testRole.name} {verdict ? "has" : "lacks"} <span className="mono">{check.permission.trim()}</span>. Per-user deny overrides still win.</div>}
        </div>
      </div>
      {loading ? <Loading /> : (
        <div className="grid cols-2">{(data || []).map((r) => (
          <div key={r.id} className="card">
            <div className="row between"><h3>{r.name}</h3><div className="row"><span className="mono muted">{r.key}</span>{r.is_system_role && <Badge kind="gold">system</Badge>}</div></div>
            <div className="row mt">{rolePerms(r).map((p: string) => <Badge key={p} kind={p.includes("*") ? "gold" : "info"}>{p}</Badge>)}</div>
            <div className="row between mt"><span className="muted">{holders(r.id).length} user{holders(r.id).length === 1 ? "" : "s"}</span>
              {holders(r.id).length > 0 && <button className="sm ghost" onClick={() => setRoleFilter(roleFilter === r.id ? "" : r.id)}>{roleFilter === r.id ? "Show all" : "Who has it"}</button>}</div>
          </div>
        ))}</div>
      )}
      <div className="card table-wrap mt">
        <div className="row between"><h3>Active assignments{roleFilter && ` — ${(data || []).find((r) => r.id === roleFilter)?.name}`}</h3>{roleFilter && <button className="sm ghost" onClick={() => setRoleFilter("")}>Show all</button>}</div>
        <ErrorNote error={assignments.error} />
        {!(assignments.data || []).length ? <Empty>No one holds a role yet. Assign roles from Users &amp; roles.</Empty> : (
          <table className="mt"><thead><tr><th>User</th><th>Role</th><th>Assigned by</th><th>When</th><th /></tr></thead>
            <tbody>{(assignments.data || []).filter((a) => !roleFilter || a.role_id === roleFilter).map((a) => {
              const u = who(a.user_id);
              const r = (data || []).find((x) => x.id === a.role_id);
              return (
                <tr key={a.id}>
                  <td>{u ? <><b>{u.display_name}</b><div className="muted">{u.email}</div></> : <span className="mono muted">{a.user_id}</span>}{u?.status === "suspended" && <Badge kind="warn">suspended</Badge>}</td>
                  <td><span className="badge gold">{r?.key || a.role_id}</span></td>
                  <td>{a.assigned_by.startsWith("ai:") ? <Badge kind="info">{a.assigned_by}</Badge> : a.assigned_by}</td>
                  <td className="muted">{a.assigned_at ? fmtTime(a.assigned_at) : "—"}</td>
                  <td><button className="sm ghost" onClick={() => confirm(`Revoke ${r?.key} from ${u?.display_name || a.user_id}?`) && run("Role revoked", () => must(post(`/api/v1/rbac/role-assignments/${a.id}/revoke`, { revoked_by: me?.email || "super.admin", reason: "super admin" }))).then(assignments.reload)}>Revoke</button></td>
                </tr>
              );
            })}</tbody></table>
        )}
      </div>
    </>
  );
}

// ── Tenants ───────────────────────────────────────────────────────────
export function Tenants() {
  const run = useAction();
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get("/api/v1/rbac/tenants")));
  const [form, setForm] = useState({ tenant_key: "", name: "", subsidiary: "" });
  return (
    <>
      <PageHead title="Tenants" text="Every tenant across the platform's subsidiaries. Suspending a tenant is recorded in the platform audit log." />
      <ErrorNote error={error} />
      <div className="card mb">
        <div className="form">
          <label className="field">Tenant key<input placeholder="voyqi-texas" value={form.tenant_key} onChange={(e) => setForm({ ...form, tenant_key: e.target.value })} /></label>
          <label className="field">Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field">Subsidiary<input placeholder="mobility, dealer, capital…" value={form.subsidiary} onChange={(e) => setForm({ ...form, subsidiary: e.target.value })} /></label>
        </div>
        <button className="mt" disabled={!form.tenant_key || !form.name || !form.subsidiary} onClick={() => run("Tenant created", () => must(post("/api/v1/rbac/tenants", form))).then(() => { setForm({ tenant_key: "", name: "", subsidiary: "" }); reload(); })}>Create tenant</button>
      </div>
      {loading ? <Loading /> : !(data || []).length ? <Empty>No tenants yet.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Tenant</th><th>Subsidiary</th><th>Status</th><th /></tr></thead>
          <tbody>{(data || []).map((t) => (
            <tr key={t.id}><td><b>{t.name}</b><div className="mono muted">{t.tenant_key}</div></td><td>{t.subsidiary}</td><td><StatusBadge value={t.status} /></td>
              <td>{t.status === "suspended"
                ? <button className="sm ok" onClick={() => run("Reactivated", () => must(post(`/api/v1/rbac/tenants/${t.id}/reactivate`))).then(reload)}>Reactivate</button>
                : <button className="sm danger" onClick={() => confirm(`Suspend ${t.name}?`) && run("Suspended", () => must(post(`/api/v1/rbac/tenants/${t.id}/suspend`))).then(reload)}>Suspend</button>}</td></tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}

// ── Permission overrides ──────────────────────────────────────────────
export function Overrides() {
  const run = useAction();
  const { me } = useSession();
  const actor = me?.email || "super.admin";
  const list = useLoad(() => must<any[]>(get("/api/v1/rbac/permission-overrides")));
  const users = useLoad(() => must<any[]>(get("/api/v1/rbac/users")));
  const [f, setF] = useState({ user_id: "", permission_key: "", effect: "allow", reason: "" });
  const [show, setShow] = useState("active");
  const [threshold, setThreshold] = useState(50);
  const who = (id: string) => (users.data || []).find((u) => u.id === id);
  const rows = (list.data || []).filter((o) => (show === "all" ? true : show === "flagged" ? o.risk_flagged && !o.revoked : show === "revoked" ? o.revoked : !o.revoked));
  const revoke = (o: any) => {
    const reason = prompt(`Why revoke ${o.effect} ${o.permission_key}?`);
    if (reason) run("Override revoked", () => must(post(`/api/v1/rbac/permission-overrides/${o.id}/revoke`, { revoked_by: actor, reason }))).then(list.reload);
  };
  const review = (o: any) => run("Risk reviewed", async () => {
    const r = await must<any>(post(`/api/v1/rbac/permission-overrides/${o.id}/auto-revoke-if-unwarranted`, { threshold, revoked_by: actor }));
    if (!r.revoked && r.risk_flagged) throw new Error("Scored at or above the threshold — kept and flagged for a human decision.");
  }).then(list.reload);
  return (
    <>
      <PageHead title="Permission overrides" text="Per-user exceptions to their roles. Risk review is tier 2: below the risk threshold a clearly unwarranted allow is revoked automatically; at or above it, the override is flagged for a person instead. Deny overrides are never auto-revoked — there's no unwarranted case for removing a restriction." />
      <ErrorNote error={list.error} />
      <div className="card mb">
        <h3>Grant override</h3>
        <div className="form mt">
          <label className="field">User<select value={f.user_id} onChange={(e) => setF({ ...f, user_id: e.target.value })}><option value="">— choose —</option>{(users.data || []).map((u) => <option key={u.id} value={u.id}>{u.display_name} ({u.email})</option>)}</select></label>
          <label className="field">Permission<input list="override-keys" placeholder="fleet.write" value={f.permission_key} onChange={(e) => setF({ ...f, permission_key: e.target.value })} /><datalist id="override-keys">{PERMISSION_KEYS.map((p) => <option key={p} value={p} />)}</datalist></label>
          <label className="field">Effect<select value={f.effect} onChange={(e) => setF({ ...f, effect: e.target.value })}><option value="allow">allow</option><option value="deny">deny</option></select></label>
          <label className="field">Reason<input value={f.reason} placeholder="Quarter-end close, until 31 Oct" onChange={(e) => setF({ ...f, reason: e.target.value })} /></label>
        </div>
        <button className="mt" disabled={!f.user_id || !f.permission_key} onClick={() => run("Override granted", () => must(post("/api/v1/rbac/permission-overrides", { ...f, granted_by: actor }))).then((ok) => { if (ok) { setF({ user_id: "", permission_key: "", effect: "allow", reason: "" }); list.reload(); } })}>Grant override</button>
      </div>
      <div className="row mb">
        <div className="tabs" style={{ marginBottom: 0, flex: 1 }}>
          {[["active", "Active"], ["flagged", "Flagged for review"], ["revoked", "Revoked"], ["all", "All"]].map(([k, l]) => <button key={k} className={show === k ? "on" : ""} onClick={() => setShow(k)}>{l}</button>)}
        </div>
        <label className="row muted" style={{ fontSize: 12 }}>Risk threshold <input type="number" min={0} max={100} style={{ width: 70 }} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))} /></label>
      </div>
      {list.loading ? <Loading /> : !rows.length ? <Empty>Nothing here.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>User</th><th>Override</th><th>Reason</th><th>Granted by</th><th>State</th><th /></tr></thead>
          <tbody>{rows.map((o) => (
            <tr key={o.id}>
              <td>{who(o.user_id)?.display_name || <span className="mono muted">{o.user_id.slice(0, 8)}</span>}<div className="muted">{who(o.user_id)?.email}</div></td>
              <td><Badge kind={o.effect === "deny" ? "bad" : "ok"}>{o.effect}</Badge> <span className="mono">{o.permission_key}</span></td>
              <td className="muted" style={{ maxWidth: 300 }}>{o.reason || "—"}</td>
              <td className="muted">{o.granted_by}</td>
              <td>{o.revoked ? <><Badge>revoked</Badge><div className="muted" style={{ fontSize: 12 }}>{o.revoked_by}: {o.revoked_reason}</div></> : o.risk_flagged ? <Badge kind="warn">flagged</Badge> : <Badge kind="ok">active</Badge>}</td>
              <td>{!o.revoked && <div className="row">
                {o.effect === "allow" && <button className="sm ghost" onClick={() => review(o)}>Risk review</button>}
                <button className="sm danger" onClick={() => revoke(o)}>Revoke</button>
              </div>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}

// ── ABAC policies ─────────────────────────────────────────────────────
export function Abac() {
  const run = useAction();
  const { me } = useSession();
  const abac = useLoad(() => must<any[]>(get("/api/v1/rbac/abac-policies")));
  const roles = useLoad(() => must<any[]>(get("/api/v1/rbac/roles")));
  const [f, setF] = useState({ name: "", source_role_key: "", target_bundle_key: "" });
  const [applying, setApplying] = useState<any>(null);
  const [acct, setAcct] = useState("");
  const roleName = (k: string) => (roles.data || []).find((r) => r.key === k)?.name || k;
  const create = () => run("Draft created — simulating", async () => {
    const p = await must<any>(post("/api/v1/rbac/abac-policies", f));
    await must(post(`/api/v1/rbac/abac-policies/${p.id}/simulate`));
  }).then((ok) => { if (ok) { setF({ name: "", source_role_key: "", target_bundle_key: "" }); abac.reload(); } });
  const apply = () => run("Policy applied", () => must(post(`/api/v1/rbac/abac-policies/${applying.id}/apply`, { accountable_role: acct.trim(), applied_by: me?.email || "super.admin" })))
    .then((ok) => { if (ok) { setApplying(null); setAcct(""); abac.reload(); } });
  return (
    <>
      <PageHead title="ABAC policies" text="A policy lets one role inherit another's permission bundle. Simulation is tier 1 — run it as often as you like, it never changes what anyone can do. Applying is a hard tier 3 gate: it needs a prior simulation and a named accountable role, and no risk score can reach it." />
      <ErrorNote error={abac.error} />
      <div className="card mb">
        <h3>Author policy</h3>
        <div className="form mt">
          <label className="field">Policy name<input value={f.name} placeholder="Fleet leads read rentals" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="field">Source role (gets the access)<select value={f.source_role_key} onChange={(e) => setF({ ...f, source_role_key: e.target.value })}><option value="">— choose —</option>{(roles.data || []).map((r) => <option key={r.id} value={r.key}>{r.name}</option>)}</select></label>
          <label className="field">Bundle to inherit<select value={f.target_bundle_key} onChange={(e) => setF({ ...f, target_bundle_key: e.target.value })}><option value="">— choose —</option>{(roles.data || []).filter((r) => r.key !== f.source_role_key).map((r) => <option key={r.id} value={r.key}>{r.name}</option>)}</select></label>
        </div>
        <button className="mt" disabled={!f.name || !f.source_role_key || !f.target_bundle_key} onClick={create}>Create draft & simulate</button>
      </div>
      {applying && (
        <div className="card mb" style={{ borderColor: "var(--bad)" }}>
          <h3>Apply “{applying.name}” — tier 3</h3>
          <div className="sub">{roleName(applying.source_role_key)} will inherit everything {roleName(applying.target_bundle_key)} can do. {applying.conflicts_detected?.length ? `The simulation found ${applying.conflicts_detected.length} conflict(s) — applying forces past them.` : "The simulation found no conflicts."}</div>
          <div className="row mt">
            <input style={{ flex: 1 }} placeholder="Accountable role, e.g. compliance_officer" value={acct} onChange={(e) => setAcct(e.target.value)} />
            <button className="danger" disabled={!acct.trim()} onClick={apply}>Apply as accountable</button>
            <button className="ghost" onClick={() => setApplying(null)}>Cancel</button>
          </div>
        </div>
      )}
      {abac.loading ? <Loading /> : !(abac.data || []).length ? <Empty>No ABAC policies yet.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Policy</th><th>Inheritance</th><th>Simulation</th><th>Status</th><th /></tr></thead>
          <tbody>{(abac.data || []).map((p) => (
            <tr key={p.id}>
              <td><b>{p.name}</b>{p.applied_by && <div className="muted">applied by {p.applied_by} as {p.accountable_role}</div>}</td>
              <td>{roleName(p.source_role_key)} <span className="muted">→ inherits</span> {roleName(p.target_bundle_key)}</td>
              <td>{p.status === "draft" ? <span className="muted">not run</span> : p.conflicts_detected?.length ? <span title={JSON.stringify(p.conflicts_detected, null, 2)}><Badge kind="warn">{p.conflicts_detected.length} conflicts</Badge></span> : <Badge kind="ok">clean</Badge>}</td>
              <td><StatusBadge value={p.status} /></td>
              <td>{!["applied", "active", "rejected"].includes(p.status) && <div className="row">
                <button className="sm ghost" onClick={() => run("Simulated", () => must(post(`/api/v1/rbac/abac-policies/${p.id}/simulate`))).then(abac.reload)}>Simulate</button>
                <button className="sm" disabled={p.status === "draft"} title={p.status === "draft" ? "Simulate first" : ""} onClick={() => setApplying(p)}>Apply…</button>
                <button className="sm ghost" onClick={() => run("Rejected", () => must(post(`/api/v1/rbac/abac-policies/${p.id}/reject?actor_id=${encodeURIComponent(me?.email || "super.admin")}`))).then(abac.reload)}>Reject</button>
              </div>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}

// ── Module & panel grants ─────────────────────────────────────────────
export function Modules() {
  const run = useAction();
  const { me } = useSession();
  const actor = me?.email || "super.admin";
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get("/api/v1/rbac/module-grants")));
  const [custom, setCustom] = useState({ key: "", parent: "" });
  // A switch shows its new state while the change is being saved.
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const grant = (k: string) => (data || []).find((g) => g.module_key === k);
  const on = (k: string) => pending[k] ?? !!grant(k)?.enabled;
  const settle = (k: string) => reload().finally(() => setPending(({ [k]: _, ...rest }) => rest));
  const enable = (module_key: string, label: string, parent_module_key?: string) => {
    setPending((p) => ({ ...p, [module_key]: true }));
    return run(`${label} enabled`, () => must(post("/api/v1/rbac/module-grants", { module_key, label, parent_module_key: parent_module_key || null, granted_by: actor }))).then(() => settle(module_key));
  };
  const disable = (module_key: string, label: string, isParent: boolean) => {
    if (isParent && !confirm(`Disable ${label}? Every one of its sections is disabled with it.`)) return;
    setPending((p) => ({ ...p, [module_key]: false }));
    return run(`${label} disabled`, () => must(post(`/api/v1/rbac/module-grants/${encodeURIComponent(module_key)}/revoke?actor_id=${encodeURIComponent(actor)}`))).then(() => settle(module_key));
  };
  const known = new Set(CONSOLES.flatMap((c) => [c.key, ...c.sections.map(([k]) => k)]));
  const extra = (data || []).filter((g) => !known.has(g.module_key));
  return (
    <>
      <PageHead title="Modules & panels" text="Which consoles — and which sections inside them — this tenant has. A section can only be enabled while its console is; disabling a console disables every section under it. Provisioning isn't an access decision: who may use a console is still each user's roles." />
      <ErrorNote error={error} />
      {loading ? <Loading /> : (
        <div className="grid cols-2">{CONSOLES.map((c) => (
          <div key={c.key} className="card" style={on(c.key) ? { borderColor: "var(--gold)" } : undefined}>
            <div className="row between">
              <div><h3>{c.label}</h3><div className="mono muted">{c.key}</div></div>
              {on(c.key) ? <button className="sm danger" onClick={() => disable(c.key, c.label, true)}>Disable</button> : <button className="sm ok" onClick={() => enable(c.key, c.label)}>Enable</button>}
            </div>
            <div className="mt">{c.sections.map(([k, label]) => (
              <label key={k} className="row" style={{ padding: "3px 0", opacity: on(c.key) ? 1 : 0.5 }}>
                <input type="checkbox" disabled={!on(c.key)} checked={on(k)} onChange={(e) => (e.target.checked ? enable(k, label, c.key) : disable(k, label, false))} />
                {label}
              </label>
            ))}</div>
            {grant(c.key) && <div className="muted mt" style={{ fontSize: 12 }}>Last changed by {grant(c.key).granted_by} · {fmtTime(grant(c.key).granted_at)}</div>}
          </div>
        ))}</div>
      )}
      <div className="card mt">
        <h3>Other modules</h3>
        <div className="row mt">
          <input placeholder="module key" value={custom.key} onChange={(e) => setCustom({ ...custom, key: e.target.value })} />
          <select value={custom.parent} onChange={(e) => setCustom({ ...custom, parent: e.target.value })}><option value="">top level</option>{(data || []).filter((g) => g.enabled && !g.parent_module_key).map((g) => <option key={g.id} value={g.module_key}>under {g.label || g.module_key}</option>)}</select>
          <button className="ghost" disabled={!custom.key.trim()} onClick={() => enable(custom.key.trim(), custom.key.trim(), custom.parent).then(() => setCustom({ key: "", parent: "" }))}>Enable</button>
        </div>
        {extra.length > 0 && <table className="mt"><tbody>{extra.map((g) => (
          <tr key={g.id}><td className="mono">{g.module_key}</td><td className="muted">{g.parent_module_key ? `under ${g.parent_module_key}` : "top level"}</td><td>{g.enabled ? <Badge kind="ok">enabled</Badge> : <Badge>disabled</Badge>}</td>
            <td>{g.enabled && <button className="sm ghost" onClick={() => disable(g.module_key, g.module_key, !g.parent_module_key)}>Disable</button>}</td></tr>
        ))}</tbody></table>}
      </div>
    </>
  );
}
