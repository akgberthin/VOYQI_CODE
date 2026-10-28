"use client";

import Link from "next/link";
import { get, must } from "@/lib/api";
import { Badge, ErrorNote, Loading, PageHead, fmtTime, pretty, useLoad } from "@/lib/ui";
import { readiness } from "./Messaging";
import { loadServices } from "./Services";

export default function Overview() {
  // Service health takes a few seconds (every service is probed), so it
  // loads on its own and the rest of the page doesn't wait for it.
  const health = useLoad(loadServices);
  const { data, error, loading, reload } = useLoad(async () => {
    const [agents, pending, decisions, policy, users, integrations, tenants, overrides, abac, grants, audit] = await Promise.all([
      must<any[]>(get("/api/v1/ai/agents/registry")),
      must<any[]>(get("/api/v1/ai/recommendations?status=pending")),
      must<any[]>(get("/api/v1/ai/decision-log?limit=12")),
      must<any>(get("/api/v1/ai/tier-policy")),
      must<any[]>(get("/api/v1/rbac/users")),
      must<any[]>(get("/api/v1/rbac/integrations")).catch(() => []),
      must<any[]>(get("/api/v1/rbac/tenants")).catch(() => []),
      must<any[]>(get("/api/v1/rbac/permission-overrides")).catch(() => []),
      must<any[]>(get("/api/v1/rbac/abac-policies")).catch(() => []),
      must<any[]>(get("/api/v1/rbac/module-grants")).catch(() => []),
      must<any[]>(get("/api/v1/rbac/audit-log")).catch(() => []),
    ]);
    return { agents, pending, decisions, policy, users, integrations, tenants, overrides, abac, grants, audit };
  });
  if (loading && !data) return <Loading what="Reading the whole platform" />;
  if (!data) return <ErrorNote error={error} />;
  const services = health.data || [];
  const down = services.filter((s) => s.status === "down");
  const slow = services.filter((s) => s.status === "timeout");
  const stopped = data.agents.filter((a) => a.status !== "active");
  const fallback = data.decisions.some((d) => (d.reasoning || "").startsWith("[deterministic fallback"));
  const tiers = [1, 2, 3].map((t) => data.policy.actions.filter((a: any) => a.tier === t).length);
  const flagged = data.overrides.filter((o) => o.risk_flagged && !o.revoked).length;
  return (
    <>
      <PageHead title="Platform overview" text="Live state of every service and AI agent, through the API gateway.">
        <button className="ghost" onClick={() => { reload(); health.reload(); }}>Refresh</button>
      </PageHead>
      <div className="grid cols-4 mb">
        <Link href="/admin/services" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Services healthy</div><div className="n" style={{ color: down.length ? "var(--bad)" : slow.length ? "var(--warn)" : "var(--ok)" }}>{health.data ? `${services.length - down.length - slow.length}/${services.length}` : <span className="spin" />}</div></Link>
        <Link href="/admin/ai/agents" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">AI agents running</div><div className="n">{data.agents.length - stopped.length}/{data.agents.length}</div></Link>
        <Link href="/admin/ai/approvals" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Waiting for approval</div><div className="n" style={{ color: data.pending.length ? "var(--warn)" : undefined }}>{data.pending.length}</div></Link>
        <Link href="/admin/access/users" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Platform users</div><div className="n">{data.users.length}</div></Link>
      </div>
      {down.length > 0 && <div className="notice bad mb">Down: {down.map((s) => s.name).join(", ")}</div>}
      {slow.length > 0 && <div className="notice warn mb">Slow to answer (overloaded, not necessarily down): {slow.map((s) => s.name).join(", ")}</div>}
      {stopped.length > 0 && <div className="notice warn mb">Not running: {stopped.map((a) => `${a.display_name} (${a.status})`).join(", ")}</div>}
      <Link href="/admin/messaging" className="card mb" style={{ display: "block", textDecoration: "none", color: "inherit" }}>
        <div className="row between"><b>Outward channels</b><span className="muted">Messaging & automation →</span></div>
        <div className="row mt">{([["AI", "anthropic"], ["Email", "smtp"], ["SMS & voice", "twilio"], ["Push", "fcm"], ["Meta", "meta"], ["LinkedIn", "linkedin"], ["X", "x"], ["Images", "openai"]] as [string, string][]).map(([label, provider]) => {
          const [kind, text] = readiness(data.integrations, provider);
          return <Badge key={provider} kind={kind}>{label}: {text.split(" — ")[0]}</Badge>;
        })}</div>
      </Link>
      <div className="notice info mb">AI reasoning: {fallback ? "rule-based fallback — no Claude API key with credits configured" : "live Claude"}. Autonomy: {tiers[0]} actions autonomous · {tiers[1]} need approval · {tiers[2]} need a named person.</div>
      <div className="grid cols-4 mb">
        <Link href="/admin/access/tenants" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Tenants</div><div className="n">{data.tenants.length}</div><div className="muted">{data.tenants.filter((t) => t.status === "suspended").length} suspended</div></Link>
        <Link href="/admin/access/overrides" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Overrides to review</div><div className="n" style={{ color: flagged ? "var(--warn)" : undefined }}>{flagged}</div><div className="muted">{data.overrides.filter((o) => !o.revoked).length} active · tier 2</div></Link>
        <Link href="/admin/access/abac" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">ABAC policies applied</div><div className="n">{data.abac.filter((p) => p.status === "applied").length}</div><div className="muted">{data.abac.filter((p) => p.status === "simulated").length} simulated, awaiting tier 3</div></Link>
        <Link href="/admin/access/modules" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Modules provisioned</div><div className="n">{data.grants.filter((g) => g.enabled && !g.parent_module_key).length}</div><div className="muted">{data.grants.filter((g) => g.enabled && g.parent_module_key).length} sections</div></Link>
      </div>
      <div className="row mb">
        <Badge kind="ok">T1 · auto — runs immediately, logged</Badge>
        <Badge kind="warn">T2 · risk review — below threshold acts, at/above waits for a person</Badge>
        <Badge kind="bad">T3 · always human — needs a named accountable role</Badge>
      </div>
      <div className="grid cols-2">
        <div className="card">
          <div className="row between"><h3>Latest AI decisions</h3><Link href="/admin/ai/decisions">All</Link></div>
          <table className="mt"><tbody>{data.decisions.map((d) => (
            <tr key={d.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(d.created_at)}</td><td>{pretty(d.agent_key)}</td><td className="mono">{d.trigger_event_type}</td></tr>
          ))}</tbody></table>
        </div>
        <div className="card">
          <div className="row between"><h3>Waiting for a human</h3><Link href="/admin/ai/approvals">Open queue</Link></div>
          {data.pending.length ? (
            <table className="mt"><tbody>{data.pending.slice(0, 10).map((r) => (
              <tr key={r.id}><td>{pretty(r.agent_key)}</td><td><Badge kind="ai">{pretty(r.action_type)}</Badge></td><td>{r.requires_accountable_role && <Badge kind="bad">tier 3</Badge>}</td></tr>
            ))}</tbody></table>
          ) : <div className="empty">Nothing waiting.</div>}
        </div>
      </div>
      <div className="card mt">
        <div className="row between"><h3>Recent platform activity</h3><Link href="/admin/security/audit">Audit log</Link></div>
        {data.audit.length ? (
          <table className="mt"><tbody>{data.audit.slice(0, 8).map((e) => (
            <tr key={e.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.occurred_at)}</td><td><Badge kind={e.actor_type === "ai" ? "ai" : ""}>{e.actor_type}</Badge> {e.actor_id}</td><td className="mono">{e.action}</td><td className="mono muted clip">{e.resource}</td></tr>
          ))}</tbody></table>
        ) : <div className="empty">No activity yet.</div>}
      </div>
    </>
  );
}
