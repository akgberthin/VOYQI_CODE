import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { onExpired, post } from "./api";
import { Login } from "./Login";
import { SessionCtx, type Me } from "./session";
import { ToastHost } from "./ui";

export type NavGroup = { title: string; links: { to: string; label: string; badge?: number }[] };

function loadMe(key: string): Me | null {
  try { return JSON.parse(sessionStorage.getItem(key) || "null"); } catch { return null; }
}

/** Topbar + sidebar + session handling shared by every VOYQI console. */
export function Shell({ app, title, defaultEmail, verify, nav, children }: {
  app: string; title: string; defaultEmail?: string; verify: () => Promise<string | null>;
  nav: (me: Me) => NavGroup[]; children: ReactNode;
}) {
  const key = `voyqi.${app}.me`;
  const [me, setMe] = useState<Me | null>(() => loadMe(key));
  const remember = (m: Me | null) => {
    setMe(m);
    try { m ? sessionStorage.setItem(key, JSON.stringify(m)) : sessionStorage.removeItem(key); } catch { /* private mode */ }
  };
  useEffect(() => onExpired(() => remember(null)), []);
  const logout = async () => { await post("/api/v1/auth/logout"); remember(null); };

  if (!me) return <ToastHost><Login title={title} defaultEmail={defaultEmail} verify={verify} onLogin={remember} /></ToastHost>;
  const initials = me.email.split("@")[0].split(/[._-]/).map((p) => p[0]?.toUpperCase()).join("").slice(0, 2);
  return (
    <SessionCtx.Provider value={{ me, logout }}>
      <ToastHost>
        <div className="shell">
          <header className="topbar">
            <span className="brand">VOYQI</span>
            <span className="where">{title} · tenant {me.tenant_id}</span>
            <span className="spacer" />
            <div className="who"><span className="avatar">{initials}</span><span>{me.email}</span><button className="sm ghost" onClick={logout}>Sign out</button></div>
          </header>
          <nav className="side">
            {nav(me).map((g) => (
              <div key={g.title}>
                <h6>{g.title}</h6>
                {g.links.map((l) => (
                  <NavLink key={l.to} to={l.to} end={l.to === "/"}>{l.label}{!!l.badge && <span className="count">{l.badge}</span>}</NavLink>
                ))}
              </div>
            ))}
          </nav>
          <main className="main">{children}</main>
        </div>
      </ToastHost>
    </SessionCtx.Provider>
  );
}
