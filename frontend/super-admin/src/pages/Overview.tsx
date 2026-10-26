import { Link } from "react-router-dom";
import { get, must } from "../api";
import { Badge, ErrorNote, Loading, PageHead, fmtTime, pretty, useLoad } from "../components/ui";
import { loadServices } from "./Services";

export default function Overview() {
  // Service health takes a few seconds (every service is probed), so it
  // loads on its own and the rest of the page doesn't wait for it.
  const health = useLoad(loadServices);
  const { data, error, loading, reload } = useLoad(async () => {
    const [agents, pending, decisions, policy, users] = await Promise.all([
      must<any[]>(get("/api/v1/ai/agents/registry")),
      must<any[]>(get("/api/v1/ai/recommendations?status=pending")),
      must<any[]>(get("/api/v1/ai/decision-log?limit=12")),
      must<any>(get("/api/v1/ai/tier-policy")),
      must<any[]>(get("/api/v1/rbac/users")),
    ]);
    return { agents, pending, decisions, policy, users };
  });
  if (loading && !data) return <Loading what="Reading the whole platform" />;
  if (!data) return <ErrorNote error={error} />;
  const services = health.data || [];
  const down = services.filter((s) => s.status === "down");
  const slow = services.filter((s) => s.status === "timeout");
  const stopped = data.agents.filter((a) => a.status !== "active");
  const fallback = data.decisions.some((d) => (d.reasoning || "").startsWith("[deterministic fallback"));
  const tiers = [1, 2, 3].map((t) => data.policy.actions.filter((a: any) => a.tier === t).length);
  return (
    <>
      <PageHead title="Platform overview" text="Live state of every service and AI agent, through the API gateway.">
        <button className="ghost" onClick={() => { reload(); health.reload(); }}>Refresh</button>
      </PageHead>
      <div className="grid cols-4 mb">
        <Link to="/services" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Services healthy</div><div className="n" style={{ color: down.length ? "var(--bad)" : slow.length ? "var(--warn)" : "var(--ok)" }}>{health.data ? `${services.length - down.length - slow.length}/${services.length}` : <span className="spin" />}</div></Link>
        <Link to="/ai/agents" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">AI agents running</div><div className="n">{data.agents.length - stopped.length}/{data.agents.length}</div></Link>
        <Link to="/ai/approvals" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Waiting for approval</div><div className="n" style={{ color: data.pending.length ? "var(--warn)" : undefined }}>{data.pending.length}</div></Link>
        <Link to="/access/users" className="card stat" style={{ textDecoration: "none", color: "inherit" }}><div className="l">Platform users</div><div className="n">{data.users.length}</div></Link>
      </div>
      {down.length > 0 && <div className="notice bad mb">Down: {down.map((s) => s.name).join(", ")}</div>}
      {slow.length > 0 && <div className="notice warn mb">Slow to answer (overloaded, not necessarily down): {slow.map((s) => s.name).join(", ")}</div>}
      {stopped.length > 0 && <div className="notice warn mb">Not running: {stopped.map((a) => `${a.display_name} (${a.status})`).join(", ")}</div>}
      <div className="notice info mb">AI reasoning: {fallback ? "rule-based fallback — no Claude API key with credits configured" : "live Claude"}. Autonomy: {tiers[0]} actions autonomous · {tiers[1]} need approval · {tiers[2]} need a named person.</div>
      <div className="grid cols-2">
        <div className="card">
          <div className="row between"><h3>Latest AI decisions</h3><Link to="/ai/decisions">All</Link></div>
          <table className="mt"><tbody>{data.decisions.map((d) => (
            <tr key={d.id}><td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(d.created_at)}</td><td>{pretty(d.agent_key)}</td><td className="mono">{d.trigger_event_type}</td></tr>
          ))}</tbody></table>
        </div>
        <div className="card">
          <div className="row between"><h3>Waiting for a human</h3><Link to="/ai/approvals">Open queue</Link></div>
          {data.pending.length ? (
            <table className="mt"><tbody>{data.pending.slice(0, 10).map((r) => (
              <tr key={r.id}><td>{pretty(r.agent_key)}</td><td><Badge kind="ai">{pretty(r.action_type)}</Badge></td><td>{r.requires_accountable_role && <Badge kind="bad">tier 3</Badge>}</td></tr>
            ))}</tbody></table>
          ) : <div className="empty">Nothing waiting.</div>}
        </div>
      </div>
    </>
  );
}
