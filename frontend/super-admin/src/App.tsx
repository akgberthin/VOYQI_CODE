import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { get, onExpired, post } from "./api";
import { ToastHost } from "./components/ui";
import { Policies, Roles, Tenants, Users } from "./pages/Access";
import { Agents, Approvals, DecisionLog, Goals, Prompts, TierPolicy } from "./pages/Ai";
import ApiExplorer from "./pages/ApiExplorer";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import { AuditLog, Sessions } from "./pages/Security";
import Services from "./pages/Services";
import { SessionCtx, type Me } from "./session";

const NAV: [string, [string, string][]][] = [
  ["Platform", [["/", "Overview"], ["/services", "Services & APIs"]]],
  ["AI orchestration", [["/ai/agents", "Agents"], ["/ai/approvals", "Approvals"], ["/ai/tiers", "Autonomy tiers"], ["/ai/prompts", "Prompts"], ["/ai/goals", "Goals"], ["/ai/decisions", "Decision log"]]],
  ["Access", [["/access/users", "Users & roles"], ["/access/roles", "Roles"], ["/access/policies", "Access policies"], ["/access/tenants", "Tenants"]]],
  ["Security", [["/security/sessions", "Privileged sessions"], ["/security/audit", "Audit log"]]],
];

function loadMe(): Me | null {
  try { return JSON.parse(sessionStorage.getItem("voyqi.me") || "null"); } catch { return null; }
}

export default function App() {
  const [me, setMe] = useState<Me | null>(loadMe);
  const [pending, setPending] = useState(0);
  const location = useLocation();
  const remember = (m: Me | null) => {
    setMe(m);
    try { m ? sessionStorage.setItem("voyqi.me", JSON.stringify(m)) : sessionStorage.removeItem("voyqi.me"); } catch { /* private mode */ }
  };
  useEffect(() => onExpired(() => remember(null)), []);
  useEffect(() => {
    if (!me) return;
    get("/api/v1/ai/recommendations?status=pending").then((r) => r.ok && setPending(r.data.length));
  }, [me, location.pathname]);
  const logout = async () => { await post("/api/v1/auth/logout"); remember(null); };

  if (!me) return <ToastHost><Login onLogin={remember} /></ToastHost>;
  return (
    <SessionCtx.Provider value={{ me, logout }}>
      <ToastHost>
        <div className="shell">
          <header className="topbar">
            <span className="brand">VOYQI</span>
            <span className="where">Super Admin · tenant {me.tenant_id}</span>
            <span className="spacer" />
            <div className="who"><span className="avatar">SA</span><span>{me.email}</span><button className="sm ghost" onClick={logout}>Sign out</button></div>
          </header>
          <nav className="side">
            {NAV.map(([group, links]) => (
              <div key={group}>
                <h6>{group}</h6>
                {links.map(([to, label]) => (
                  <NavLink key={to} to={to} end={to === "/"}>
                    {label}{to === "/ai/approvals" && pending > 0 && <span className="count">{pending}</span>}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
          <main className="main">
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/services" element={<Services />} />
              <Route path="/services/:name" element={<ApiExplorer />} />
              <Route path="/ai/agents" element={<Agents />} />
              <Route path="/ai/approvals" element={<Approvals />} />
              <Route path="/ai/tiers" element={<TierPolicy />} />
              <Route path="/ai/prompts" element={<Prompts />} />
              <Route path="/ai/goals" element={<Goals />} />
              <Route path="/ai/decisions" element={<DecisionLog />} />
              <Route path="/access/users" element={<Users />} />
              <Route path="/access/roles" element={<Roles />} />
              <Route path="/access/policies" element={<Policies />} />
              <Route path="/access/tenants" element={<Tenants />} />
              <Route path="/security/sessions" element={<Sessions />} />
              <Route path="/security/audit" element={<AuditLog />} />
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          </main>
        </div>
      </ToastHost>
    </SessionCtx.Provider>
  );
}
