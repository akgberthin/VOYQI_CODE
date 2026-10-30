"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api, get, must, post } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Badge, Empty, ErrorNote, Loading, PageHead, fmtTime, useAction, useLoad } from "@/lib/ui";

/** TOTP set-up / reconfigure. The new secret only replaces the old one once
    a code from the new app is confirmed, so abandoning this is harmless. */
function Mfa({ enabled, onDone }: { enabled: boolean; onDone: () => void }) {
  const run = useAction();
  const { me } = useSession();
  const [uri, setUri] = useState("");
  const [qr, setQr] = useState("");
  const [code, setCode] = useState("");
  useEffect(() => { if (uri) QRCode.toDataURL(uri, { margin: 1, width: 180 }).then(setQr).catch(() => setQr("")); else setQr(""); }, [uri]);
  const secret = uri ? new URL(uri.replace("otpauth://", "https://")).searchParams.get("secret") || "" : "";
  const start = () => run("Scan the code with your authenticator app", async () => setUri((await must<any>(post("/api/v1/auth/mfa/enroll", {}))).provisioning_uri));
  const confirm = () => run(enabled ? "Authenticator replaced" : "MFA is on", async () => {
    await must(post("/api/v1/auth/mfa/confirm", { totp_code: code.trim() }));
    // keep the platform record in step (shown on Users & roles)
    await must(api("PATCH", `/api/v1/rbac/users/${me!.user_id}`, { mfa_enabled: true, updated_by: me!.email })).catch(() => null);
  }).then((ok) => { if (ok) { setUri(""); setCode(""); onDone(); } });
  return (
    <div className="card">
      <div className="row between"><h3>Two-factor sign-in (TOTP)</h3>{enabled ? <Badge kind="ok">enabled</Badge> : <Badge kind="bad">off</Badge>}</div>
      <div className="sub">Operator accounts should use an authenticator app (Google Authenticator, 1Password, Authy…). Codes change every 30 seconds.</div>
      {!uri ? (
        <button className="mt" onClick={start}>{enabled ? "Reconfigure — move to a new app" : "Set up MFA"}</button>
      ) : (
        <div className="mt">
          <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
            {qr && <img src={qr} alt="Authenticator QR code" width={180} height={180} style={{ background: "#fff", borderRadius: 8 }} />}
            <div style={{ flex: 1 }}>
              <div className="muted" style={{ fontSize: 12 }}>Can&apos;t scan? Enter this key:</div>
              <div className="mono" style={{ wordBreak: "break-all", margin: "4px 0 12px" }}>{secret.replace(/(.{4})/g, "$1 ").trim()}</div>
              <label className="field">6-digit code from the app<input inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} /></label>
              <div className="row mt"><button disabled={code.length !== 6} onClick={confirm}>Confirm</button><button className="ghost" onClick={() => { setUri(""); setCode(""); }}>Cancel</button></div>
              {enabled && <div className="sub mt">Your current app keeps working until you confirm.</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

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
  const run = useAction();
  const [name, setName] = useState<string | null>(null);
  const account = useLoad(async () => (await must<any[]>(get("/api/v1/rbac/users"))).find((u) => u.id === me?.user_id) || null, [me?.user_id]);
  if (!me) return null;
  const user = account.data;
  const saveName = () => run("Name updated", () => must(api("PATCH", `/api/v1/rbac/users/${me.user_id}`, { display_name: name!.trim(), updated_by: me.email })))
    .then((ok) => { if (ok) { setName(null); account.reload(); data.reload(); } });
  const initials = me.email.split("@")[0].split(/[._-]/).map((p) => p[0]?.toUpperCase()).join("").slice(0, 2);
  return (
    <>
      <PageHead title="My profile" text="Your roles, the permissions the gateway grants you right now (deny always wins), and your recent actions on the platform." />
      <ErrorNote error={data.error} />
      <div className="card mb row" style={{ gap: 16 }}>
        <span className="avatar" style={{ width: 52, height: 52, fontSize: 18 }}>{initials}</span>
        <div>
          {name === null
            ? <div className="row"><h3>{user?.display_name || data.data?.user?.display_name || me.email}</h3>{user && <button className="sm ghost" onClick={() => setName(user.display_name)}>Edit name</button>}</div>
            : <div className="row"><input value={name} onChange={(e) => setName(e.target.value)} aria-label="Display name" /><button className="sm" disabled={!name.trim()} onClick={saveName}>Save</button><button className="sm ghost" onClick={() => setName(null)}>Cancel</button></div>}<div className="muted">{me.email}</div><div className="mono muted" style={{ fontSize: 11 }}>{me.user_id} · tenant {me.tenant_id}</div></div>
        <span style={{ flex: 1 }} />
        <div className="row">{me.access.admin && <Badge kind="gold">super admin console</Badge>}{me.access.marketing && <Badge kind="info">marketing console</Badge>}</div>
      </div>
      {user?.has_login !== false && <div className="mb"><Mfa enabled={!!(user?.login_mfa_enabled ?? user?.mfa_enabled)} onDone={account.reload} /></div>}
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
            <div className="sub mt">Sessions use an HttpOnly cookie; access tokens last 15 minutes.{user?.last_login_at && <> Last sign-in {fmtTime(user.last_login_at)}.</>}</div>
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
