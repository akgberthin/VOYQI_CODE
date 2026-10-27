import { Fragment, useState } from "react";
import { del, get, must, post, put } from "../api";
import { Badge, Empty, ErrorNote, JsonView, Loading, PageHead, StatusBadge, fmtTime, pretty, useAction, useLoad } from "../components/ui";

const FALLBACK = "[deterministic fallback";
const plain = (r = "") => (r.startsWith("[") && r.includes("] ") ? r.slice(r.indexOf("] ") + 2) : r);

// ── Agents: the kill switch for every agent ───────────────────────────
export function Agents() {
  const run = useAction();
  const reg = useLoad(() => must<any[]>(get("/api/v1/ai/agents/registry")));
  const roster = useLoad(() => must<any[]>(get("/api/v1/ai/roster")));
  const setStatus = (key: string, status: string) =>
    run(`${pretty(key)} → ${status}`, () => must(put(`/api/v1/ai/agents/registry/${key}/status`, { status }))).then(reg.reload);
  const agents = reg.data || [];
  const n = (s: string) => agents.filter((a) => a.status === s).length;
  return (
    <>
      <PageHead title="AI agents" text="Every agent the platform runs. Pausing or disabling one takes effect on the next event it would have handled, for this tenant only — and every change is recorded by the Compliance Agent.">
        <Badge kind="ok">{n("active")} active</Badge><Badge kind="warn">{n("paused")} paused</Badge><Badge kind="bad">{n("disabled")} disabled</Badge>
        <button className="ghost" onClick={reg.reload}>Refresh</button>
      </PageHead>
      <ErrorNote error={reg.error} />
      {reg.loading && !reg.data ? <Loading /> : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Agent</th><th>Roster</th><th>Triggered by</th><th>Decisions</th><th>Status</th><th>Control</th></tr></thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.key}>
                  <td><b>{a.display_name}</b><div className="muted mono">{a.key}</div></td>
                  <td>{a.roster.map((r: string) => <div key={r}><Badge kind="gold">{r}</Badge></div>)}</td>
                  <td><span className="muted" title={a.triggers.join("\n")}>{a.triggers.length ? `${a.triggers.length} events` : "called via API"}</span><div className="mono muted clip" style={{ maxWidth: 260 }}>{a.triggers.slice(0, 2).join(", ")}</div></td>
                  <td>{a.decisions}</td>
                  <td><StatusBadge value={a.status} /></td>
                  <td>
                    <div className="row">
                      {a.status !== "active" && <button className="sm ok" onClick={() => setStatus(a.key, "active")}>Resume</button>}
                      {a.status === "active" && <button className="sm ghost" onClick={() => setStatus(a.key, "paused")}>Pause</button>}
                      {a.status !== "disabled" && <button className="sm danger" onClick={() => setStatus(a.key, "disabled")}>Disable</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h3 className="mt">Roster</h3>
      <div className="grid cols-3">
        {(roster.data || []).map((r) => (
          <div key={r.name} className="card">
            <div className="row between"><h3>{r.name}</h3><div className="row"><Badge kind={r.tier === "realtime" ? "info" : ""}>{r.tier}</Badge><StatusBadge value={r.status === "implemented" ? "active" : r.status} /></div></div>
            <div className="sub mt">{r.covers}</div>
            {r.missing && <div className="notice warn mt">{r.missing}</div>}
          </div>
        ))}
      </div>
    </>
  );
}

// ── Tier policy ───────────────────────────────────────────────────────
export function TierPolicy() {
  const run = useAction();
  const { data, error, loading, reload } = useLoad(() => must<any>(get("/api/v1/ai/tier-policy")));
  const [filter, setFilter] = useState("");
  const [evidence, setEvidence] = useState<Record<string, any>>({});
  const acts: any[] = (data?.actions || []).filter((a: any) => `${a.policy_agent} ${a.action_type}`.includes(filter));
  async function change(a: any, tier: number) {
    const reason = prompt(`Why move ${a.action_type} to tier ${tier}?`);
    if (!reason) return;
    await run(`${a.action_type} → tier ${tier}`, () => must(put(`/api/v1/ai/tier-policy/${a.policy_agent}/${a.action_type}`, { tier, reason })));
    reload();
  }
  const revert = (a: any) => run(`${a.action_type} back to policy file`, () => must(del(`/api/v1/ai/tier-policy/${a.policy_agent}/${a.action_type}`))).then(reload);
  const loadEvidence = async (a: any) => {
    const e = await must(get(`/api/v1/ai/tier-policy/${a.policy_agent}/${a.action_type}/evidence`));
    setEvidence((x) => ({ ...x, [a.policy_agent + a.action_type]: e }));
  };
  return (
    <>
      <PageHead title="Autonomy tiers" text="Tier 1 runs on its own, tier 2 waits for approval, tier 3 needs a named accountable person. Making an action stricter is always allowed; loosening it needs 90 days of approval evidence; tier 3 is fixed in the policy file.">
        <input placeholder="Filter actions…" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </PageHead>
      <ErrorNote error={error} />
      {loading ? <Loading /> : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Agent policy</th><th>Action</th><th>Policy file</th><th>Effective</th><th>Change</th><th>Evidence</th></tr></thead>
            <tbody>
              {acts.map((a) => {
                const ev = evidence[a.policy_agent + a.action_type];
                return (
                  <tr key={a.policy_agent + a.action_type}>
                    <td className="mono">{a.policy_agent}</td>
                    <td className="mono">{a.action_type}{a.threshold && <div className="muted">when {a.threshold}</div>}</td>
                    <td><Badge>tier {a.base_tier}</Badge></td>
                    <td><Badge kind={a.tier === 1 ? "ok" : a.tier === 2 ? "warn" : "bad"}>tier {a.tier}</Badge>{a.override && <div className="muted" title={a.override.reason}>changed by {a.override.changed_by}</div>}</td>
                    <td>
                      {a.changeable ? (
                        <div className="row">
                          {a.tier !== 1 && <button className="sm ghost" onClick={() => change(a, 1)}>→ 1</button>}
                          {a.tier !== 2 && <button className="sm ghost" onClick={() => change(a, 2)}>→ 2</button>}
                          {a.override && <button className="sm ghost" onClick={() => revert(a)}>Revert</button>}
                        </div>
                      ) : <Badge kind="bad">locked</Badge>}
                    </td>
                    <td>{ev ? <span className={ev.sufficient ? "" : "muted"}>{ev.decided} decided · {Math.round(ev.approval_rate * 100)}%</span> : a.changeable && <button className="sm ghost" onClick={() => loadEvidence(a)}>Check</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

// ── Prompts ───────────────────────────────────────────────────────────
export function Prompts() {
  const run = useAction();
  const prompts = useLoad(() => must<any[]>(get("/api/v1/ai/prompts")));
  const versions = useLoad(() => must<any[]>(get("/api/v1/ai/policies")));
  const [open, setOpen] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const reloadAll = () => { prompts.reload(); versions.reload(); };
  return (
    <>
      <PageHead title="Agent prompts" text="Each agent's instructions, versioned per tenant. A new version must pass the server-side evaluation before it can go live; promoting it takes effect without a redeploy, and rolling back is promoting an older version." />
      <ErrorNote error={prompts.error} />
      {prompts.loading ? <Loading /> : (prompts.data || []).map((p) => {
        const mine = (versions.data || []).filter((v) => v.prompt_key === p.prompt_key).sort((a, b) => b.version - a.version);
        const isOpen = open === p.prompt_key;
        return (
          <div key={p.prompt_key} className="card mb">
            <div className="row between">
              <div><b className="mono">{p.prompt_key}</b> <span className="muted">· {pretty(p.agent_key)}</span></div>
              <div className="row">
                <Badge kind={p.active_version ? "gold" : ""}>{p.active_version ? `v${p.active_version} live` : "code default live"}</Badge>
                <Badge>{mine.length} versions</Badge>
                <button className="sm ghost" onClick={() => { setOpen(isOpen ? null : p.prompt_key); setDraft(mine[0]?.system_prompt || p.default_prompt); }}>{isOpen ? "Close" : "Open"}</button>
              </div>
            </div>
            {isOpen && (
              <div className="mt">
                {p.required?.length > 0 && <div className="muted mb">Must keep: {p.required.map((r: string) => <Badge key={r}>{r}</Badge>)}</div>}
                <textarea value={draft} onChange={(e) => setDraft(e.target.value)} style={{ minHeight: 200 }} />
                <div className="row mt"><button onClick={() => run("Draft saved", () => must(post("/api/v1/ai/policies", { prompt_key: p.prompt_key, system_prompt: draft }))).then(reloadAll)}>Save as new draft</button></div>
                {mine.length > 0 && (
                  <table className="mt">
                    <thead><tr><th>Version</th><th>Status</th><th>Evaluation</th><th>By</th><th /></tr></thead>
                    <tbody>{mine.map((v) => (
                      <tr key={v.id}>
                        <td>v{v.version}</td><td><StatusBadge value={v.status} /></td>
                        <td>{v.eval_suite_passed ? <Badge kind="ok">passed</Badge> : v.eval_summary ? <span className="muted" title={v.eval_summary}>failed</span> : <span className="muted">not run</span>}</td>
                        <td className="muted">{v.created_by}{v.approved_by && ` → ${v.approved_by}`}</td>
                        <td><div className="row">
                          <button className="sm ghost" onClick={() => run(`v${v.version} evaluated`, () => must(post(`/api/v1/ai/policies/${v.id}/evaluate`))).then(reloadAll)}>Evaluate</button>
                          {v.status !== "active" && <button className="sm" disabled={!v.eval_suite_passed} onClick={() => run(`v${v.version} is live`, () => must(post(`/api/v1/ai/policies/${v.id}/promote`))).then(reloadAll)}>Promote</button>}
                          <button className="sm ghost" onClick={() => setDraft(v.system_prompt)}>Load</button>
                        </div></td>
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

// ── Approvals ─────────────────────────────────────────────────────────
export function Approvals() {
  const run = useAction();
  const [status, setStatus] = useState("pending");
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get(`/api/v1/ai/recommendations${status ? `?status=${status}` : ""}`)), [status]);
  async function decide(r: any, approve: boolean) {
    let accountable_role: string | undefined;
    if (approve && r.requires_accountable_role) {
      accountable_role = prompt("Tier 3: the accountable role approving this (e.g. compliance_officer)") || undefined;
      if (!accountable_role) return;
    }
    const reason_code = approve ? "reviewed" : prompt("Reason for rejecting") || "rejected";
    await run(approve ? "Approved — action executed" : "Rejected", () => must(post(`/api/v1/ai/recommendations/${r.id}/${approve ? "approve" : "reject"}`, { reason_code, accountable_role })));
    reload();
  }
  return (
    <>
      <PageHead title="Approvals" text="Tier 2 and tier 3 actions wait here. Nothing in this queue has happened yet — approving runs it.">
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="pending">pending</option><option value="approved">approved</option><option value="rejected">rejected</option><option value="">all</option>
        </select>
      </PageHead>
      <ErrorNote error={error} />
      {loading ? <Loading /> : !(data || []).length ? <Empty>Nothing here.</Empty> : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Agent</th><th>Action</th><th>Why</th><th>Risk</th><th>Status</th><th /></tr></thead>
            <tbody>{(data || []).map((r) => (
              <tr key={r.id}>
                <td>{pretty(r.agent_key)}<div className="muted mono">{r.triggering_event_type}</div></td>
                <td><Badge kind="ai">{pretty(r.action_type)}</Badge>{r.requires_accountable_role && <div><Badge kind="bad">tier 3</Badge></div>}</td>
                <td style={{ maxWidth: 440 }}>{plain(r.reasoning)}</td>
                <td>{r.risk_score}</td>
                <td><StatusBadge value={r.status} />{r.decided_by && <div className="muted">{r.decided_by}</div>}</td>
                <td>{r.status === "pending" && <div className="row"><button className="sm ok" onClick={() => decide(r, true)}>Approve</button><button className="sm ghost" onClick={() => decide(r, false)}>Reject</button></div>}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}

// ── Decision log ──────────────────────────────────────────────────────
export function DecisionLog() {
  const [agent, setAgent] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get(`/api/v1/ai/decision-log?limit=200${agent ? `&agent_key=${agent}` : ""}`)), [agent]);
  const agents = useLoad(() => must<any[]>(get("/api/v1/ai/agents/registry")));
  return (
    <>
      <PageHead title="AI decision log" text="Every decision every agent made: what triggered it, what it did and why. Rule-based entries ran without a Claude key.">
        <select value={agent} onChange={(e) => setAgent(e.target.value)}>
          <option value="">all agents</option>{(agents.data || []).map((a) => <option key={a.key} value={a.key}>{a.display_name}</option>)}
        </select>
        <button className="ghost" onClick={reload}>Refresh</button>
      </PageHead>
      <ErrorNote error={error} />
      {loading ? <Loading /> : (
        <div className="card table-wrap">
          <table>
            <thead><tr><th>Time</th><th>Agent</th><th>Trigger</th><th>Action</th><th>Tier</th><th>Reasoning</th></tr></thead>
            <tbody>{(data || []).map((d) => (
              <Fragment key={d.id}>
                <tr className="click" onClick={() => setOpen(open === d.id ? null : d.id)}>
                  <td className="muted">{fmtTime(d.created_at)}</td>
                  <td>{pretty(d.agent_key)}</td>
                  <td className="mono">{d.trigger_event_type}</td>
                  <td>{pretty(d.action_taken)}</td>
                  <td>{d.tier ? <Badge>{`tier ${d.tier}`}</Badge> : ""}</td>
                  <td style={{ maxWidth: 420 }}>{(d.reasoning || "").startsWith(FALLBACK) && <Badge kind="warn">rule-based</Badge>} {plain(d.reasoning).slice(0, 160)}</td>
                </tr>
                {open === d.id && <tr><td colSpan={6}><JsonView value={d} /></td></tr>}
              </Fragment>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}

// ── Goals ─────────────────────────────────────────────────────────────
export function Goals() {
  const run = useAction();
  const { data, error, loading, reload } = useLoad(() => must<any[]>(get("/api/v1/ai/goals")));
  const agents = useLoad(() => must<any[]>(get("/api/v1/ai/agents/registry")));
  const [form, setForm] = useState({ agent_key: "", goal_key: "", target_value: "", unit: "" });
  return (
    <>
      <PageHead title="Agent goals" text="Targets the agents work toward. A change applies on the agent's next reasoning pass — no redeploy." />
      <ErrorNote error={error} />
      <div className="card mb">
        <h3>New goal</h3>
        <div className="form mt">
          <label className="field">Agent<select value={form.agent_key} onChange={(e) => setForm({ ...form, agent_key: e.target.value })}><option value="">— choose —</option>{(agents.data || []).map((a) => <option key={a.key} value={a.key}>{a.display_name}</option>)}</select></label>
          <label className="field">Goal key<input value={form.goal_key} placeholder="e.g. utilization_target" onChange={(e) => setForm({ ...form, goal_key: e.target.value })} /></label>
          <label className="field">Target<input type="number" value={form.target_value} onChange={(e) => setForm({ ...form, target_value: e.target.value })} /></label>
          <label className="field">Unit<input value={form.unit} placeholder="%, usd, days" onChange={(e) => setForm({ ...form, unit: e.target.value })} /></label>
        </div>
        <button className="mt" disabled={!form.agent_key || !form.goal_key || form.target_value === ""} onClick={() => run("Goal set", () => must(post("/api/v1/ai/goals", { ...form, target_value: Number(form.target_value) }))).then(reload)}>Add goal</button>
      </div>
      {loading ? <Loading /> : !(data || []).length ? <Empty>No goals set.</Empty> : (
        <div className="card table-wrap"><table>
          <thead><tr><th>Agent</th><th>Goal</th><th>Target</th><th>Updated by</th><th /></tr></thead>
          <tbody>{(data || []).map((g) => (
            <tr key={g.id}>
              <td>{pretty(g.agent_key)}</td><td className="mono">{g.goal_key}</td><td>{g.target_value} {g.unit}</td><td className="muted">{g.updated_by}</td>
              <td><button className="sm ghost" onClick={() => { const v = prompt(`New target for ${g.goal_key}`, String(g.target_value)); if (v !== null) run("Target updated", () => must(put(`/api/v1/ai/goals/${g.id}`, { target_value: Number(v) }))).then(reload); }}>Edit</button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </>
  );
}
