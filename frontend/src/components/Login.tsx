"use client";

import { useState, type FormEvent } from "react";
import { errorText, get, getTenant, post, setTenant } from "@/lib/api";
import type { Access, Me } from "@/lib/session";

/** Which consoles the signed-in account can use. The gateway is the judge:
    a 403 means the role lacks that module's permission. */
async function checkAccess(): Promise<Access> {
  const [admin, marketing] = await Promise.all([
    // The platform API needs `platform.read`. Asking for a service that
    // doesn't exist is cheap: past the permission check it is a 404,
    // without it a 403 — no service gets probed.
    get("/api/v1/platform/services/_access-check/openapi"),
    get("/api/v1/marketing/campaigns"),
  ]);
  return { admin: admin.status !== 403 && admin.status !== 401, marketing: marketing.ok };
}

/** One sign-in for every VOYQI console, through the API gateway. */
export function Login({ onLogin }: { onLogin: (me: Me) => void }) {
  const [tenant, setT] = useState(getTenant());
  const [email, setEmail] = useState("super.admin@voyqi.example");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setTenant(tenant.trim());
    try {
      const r = await post("/api/v1/auth/login", { email, password });
      if (!r.ok) return setError(r.status === 401 ? "Wrong email or password for this tenant." : errorText(r));
      if (r.data?.status === "mfa_required") return setError("This account needs MFA, which the console doesn't support yet.");
      const access = await checkAccess();
      if (!access.admin && !access.marketing) {
        await post("/api/v1/auth/logout");
        return setError("Signed in, but this account has no access to the super admin or marketing console.");
      }
      const me = await get("/api/v1/auth/me");
      onLogin({ ...me.data, email, access });
    } catch (err: any) {
      setError(`Can't reach the API gateway: ${err.message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <div className="brand" style={{ fontSize: 20 }}>VOYQI</div>
        <h2 style={{ margin: "6px 0 2px" }}>Console sign-in</h2>
        <div className="sub mb">Super admin and marketing, through the API gateway like every other client.</div>
        <div className="grid">
          <label className="field">Tenant<input value={tenant} onChange={(e) => setT(e.target.value)} /></label>
          <label className="field">Email<input value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" /></label>
          <label className="field">Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus /></label>
          {error && <div className="notice bad">{error}</div>}
          <button disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        </div>
      </form>
    </div>
  );
}
