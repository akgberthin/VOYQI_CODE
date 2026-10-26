import { useState } from "react";
import { get, must, post } from "../api";
import { useSession } from "../session";
import { AutoTable, Badge, Empty, ErrorNote, Loading, PageHead, StatusBadge, useAction, useLoad } from "../components/ui";

// ── Users & their roles ───────────────────────────────────────────────
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
  const actor = me?.email || "super.admin";

  return (
    <>
      <PageHead title="Users & roles" text="Every platform user in this tenant. Pick one to see their roles, their effective permissions (deny always wins) and any overrides — and change them." />
      <ErrorNote error={users.error} />
      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1.3fr)" }}>
        <div className="card table-wrap">
          {users.loading ? <Loading /> : (
            <table>
              <thead><tr><th>User</th><th>MFA</th><th>Status</th></tr></thead>
              <tbody>{(users.data || []).map((u) => (
                <tr key={u.id} className="click" onClick={() => setSel(u)} style={sel?.id === u.id ? { outline: "1px solid var(--gold)" } : undefined}>
                  <td><b>{u.display_name}</b><div className="muted">{u.email}</div></td>
                  <td>{u.mfa_enabled ? <Badge kind="ok">on</Badge> : <Badge>off</Badge>}</td>
                  <td><StatusBadge value={u.status} /></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>
        <div className="card">
          {!sel ? <Empty>Select a user.</Empty> : detail.loading && !detail.data ? <Loading /> : detail.data && (
            <>
              <h3>{sel.display_name}</h3><div className="sub mono">{sel.id}</div>
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
export function Roles() {
  const run = useAction();
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get("/api/v1/rbac/roles")));
  const [form, setForm] = useState({ key: "", name: "", permission_keys: "" });
  return (
    <>
      <PageHead title="Roles" text="Bundles of permissions. Permissions are <module>.read / .write / .* (or * for everything); the gateway checks them on every request." />
      <ErrorNote error={error} />
      <div className="card mb">
        <h3>New role</h3>
        <div className="form mt">
          <label className="field">Key<input placeholder="ops_lead" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} /></label>
          <label className="field">Name<input placeholder="Operations lead" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field" style={{ gridColumn: "span 2" }}>Permissions<input placeholder="fleet.*, rental.read, hub.read" value={form.permission_keys} onChange={(e) => setForm({ ...form, permission_keys: e.target.value })} /></label>
        </div>
        <button className="mt" disabled={!form.key || !form.name} onClick={() => run("Role created", () => must(post("/api/v1/rbac/roles", { key: form.key, name: form.name, permission_keys: form.permission_keys.split(",").map((s) => s.trim()).filter(Boolean) }))).then(() => { setForm({ key: "", name: "", permission_keys: "" }); reload(); })}>Create role</button>
      </div>
      {loading ? <Loading /> : (
        <div className="grid cols-2">{(data || []).map((r) => (
          <div key={r.id} className="card">
            <div className="row between"><h3>{r.name}</h3><div className="row"><span className="mono muted">{r.key}</span>{r.is_system_role && <Badge kind="gold">system</Badge>}</div></div>
            <div className="row mt">{[...r.permission_keys, ...r.wildcard_permissions].map((p: string) => <Badge key={p} kind={p.includes("*") ? "gold" : "info"}>{p}</Badge>)}</div>
          </div>
        ))}</div>
      )}
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

// ── Policies: overrides queue, ABAC, module grants ────────────────────
export function Policies() {
  const run = useAction();
  const overrides = useLoad(() => must<any[]>(get("/api/v1/rbac/permission-overrides")));
  const abac = useLoad(() => must<any[]>(get("/api/v1/rbac/abac-policies")));
  const grants = useLoad(() => must<any[]>(get("/api/v1/rbac/module-grants")));
  return (
    <>
      <PageHead title="Access policies" text="Permission overrides across all users, attribute-based (ABAC) policies — simulated freely, applied only by a named person — and module grants per console." />
      <h3>Permission overrides</h3>
      <div className="card mb table-wrap">{overrides.loading ? <Loading /> : <AutoTable rows={overrides.data || []} />}</div>
      <h3>ABAC policies</h3>
      <div className="card mb table-wrap">
        {abac.loading ? <Loading /> : !(abac.data || []).length ? <Empty>No ABAC policies. Create one under Services → rbac-svc.</Empty> : (
          <table><thead><tr><th>Policy</th><th>Status</th><th /></tr></thead><tbody>{(abac.data || []).map((p) => (
            <tr key={p.id}><td><b>{p.name}</b><div className="mono muted">{p.source_role_key} → {p.target_bundle_key}</div>{p.conflicts_detected?.length > 0 && <Badge kind="warn">{p.conflicts_detected.length} conflicts</Badge>}{p.applied_by && <div className="muted">applied by {p.applied_by} ({p.accountable_role})</div>}</td><td><StatusBadge value={p.status} /></td>
              <td><div className="row">
                <button className="sm ghost" onClick={() => run("Simulated", () => must(post(`/api/v1/rbac/abac-policies/${p.id}/simulate`))).then(abac.reload)}>Simulate</button>
                <button className="sm" onClick={() => { const who = prompt("Tier 3: your name/role as the accountable approver"); if (who) run("Applied", () => must(post(`/api/v1/rbac/abac-policies/${p.id}/apply`, { applied_by: who, accountable_role: who }))).then(abac.reload); }}>Apply</button>
                <button className="sm ghost" onClick={() => run("Rejected", () => must(post(`/api/v1/rbac/abac-policies/${p.id}/reject?actor_id=super.admin`))).then(abac.reload)}>Reject</button>
              </div></td></tr>
          ))}</tbody></table>
        )}
      </div>
      <h3>Module grants</h3>
      <div className="card table-wrap">{grants.loading ? <Loading /> : <AutoTable rows={grants.data || []} />}</div>
    </>
  );
}
