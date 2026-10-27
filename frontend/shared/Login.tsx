import { useState, type FormEvent } from "react";
import { errorText, get, getTenant, post, setTenant } from "./api";
import type { Me } from "./session";

/** Sign-in for any VOYQI console. `verify` runs after a successful login
    and returns an error message when the account can't use this console. */
export function Login({ title, subtitle, defaultEmail = "", verify, onLogin }: {
  title: string; subtitle?: string; defaultEmail?: string;
  verify: () => Promise<string | null>; onLogin: (me: Me) => void;
}) {
  const [tenant, setT] = useState(getTenant());
  const [email, setEmail] = useState(defaultEmail);
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
      if (r.data?.status === "mfa_required") return setError("This account needs MFA, which this console doesn't support yet.");
      const denied = await verify();
      if (denied) {
        await post("/api/v1/auth/logout");
        return setError(denied);
      }
      const me = await get("/api/v1/auth/me");
      onLogin({ ...me.data, email });
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
        <h2 style={{ margin: "6px 0 2px" }}>{title}</h2>
        <div className="sub mb">{subtitle || "Signs in through the API gateway like every other client."}</div>
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
