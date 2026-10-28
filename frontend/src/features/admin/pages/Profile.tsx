"use client";

import { get, must } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Badge, Empty, ErrorNote, Loading, PageHead, fmtTime, useLoad } from "@/lib/ui";

/** The signed-in admin: roles, what the gateway will let them do, and
    what they've done. */
export default function Profile() {
  const { me } = useSession();
  const data = useLoad(async () => {
    const [ctx, eff, audit, users] = await Promise.all([
      must<any>(get(`/api/v1/rbac/users/${me!.user_id}/context`)).catch(() => null),
      must<any>(get(`/api/v1/rbac/users/${me!.user_id}/effective-permissions`)).catch(() => null),
      must<any[]>(get("/api/v1/rbac/audit-log?actor_type=human")).catch(() => []),
      must<any[]>(get("/api/v1/rbac/users")).catch(() => []),
    ]);
    const user = users.find((u) => u.id === me!.user_id) || null;
    return { ctx, eff, user, mine: audit.filter((e) => e.actor_id === me!.email || e.actor_id === me!.user_id).slice(0, 30) };
  }, [me?.user_id]);
  if (!me) return null;
  const initials = me.email.split("@")[0].split(/[._-]/).map((p) => p[0]?.toUpperCase()).join("").slice(0, 2);
  return (
    <>
      <PageHead title="My profile" text="Your roles, the permissions the gateway grants you right now (deny always wins), and your recent actions on the platform." />
      <ErrorNote error={data.error} />
      <div className="card mb row" style={{ gap: 16 }}>
        <span className="avatar" style={{ width: 52, height: 52, fontSize: 18 }}>{initials}</span>
        <div><h3>{data.data?.user?.display_name || me.email}</h3><div className="muted">{me.email}</div><div className="mono muted" style={{ fontSize: 11 }}>{me.user_id} · tenant {me.tenant_id}</div></div>
        <span style={{ flex: 1 }} />
        <div className="row">{me.access.admin && <Badge kind="gold">super admin console</Badge>}{me.access.marketing && <Badge kind="info">marketing console</Badge>}</div>
      </div>
      {data.loading ? <Loading /> : (
        <div className="split">
          <div className="card">
            <h3>Roles</h3>
            <div className="row mt">{data.data?.ctx?.assignments?.length ? data.data.ctx.assignments.map((a: any) => <Badge key={a.id} kind="gold">{a.role_key}</Badge>) : <span className="muted">No roles found for this account in rbac-svc.</span>}</div>
            <h3 className="mt">Effective permissions</h3>
            <div className="row mt">
              {(data.data?.eff?.effective_permissions || []).map((p: string) => <Badge key={p} kind={p.includes("*") ? "gold" : "info"}>{p}</Badge>)}
              {(data.data?.eff?.denied || []).map((p: string) => <Badge key={p} kind="bad">✕ {p}</Badge>)}
              {!data.data?.eff && <span className="muted">Unavailable.</span>}
            </div>
            <div className="sub mt">MFA: {data.data?.user?.mfa_enabled ? "on" : "off"} · sessions use an HttpOnly cookie; access tokens last 15 minutes.</div>
          </div>
          <div className="card">
            <h3>My recent actions</h3>
            {!data.data?.mine.length ? <Empty>Nothing recorded yet.</Empty> : (
              <table className="mt"><tbody>{data.data.mine.map((e) => (
                <tr key={e.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.occurred_at)}</td><td className="mono">{e.action}</td><td className="mono muted clip">{e.resource}</td></tr>
              ))}</tbody></table>
            )}
          </div>
        </div>
      )}
    </>
  );
}
