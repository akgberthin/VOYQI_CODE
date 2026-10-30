/* Operator notifications: things on this tenant that need a person,
   gathered live from the services (nothing is stored twice). Read state
   is per operator and per browser — a convenience, not a record. */
import { get } from "@/lib/api";

export type Alert = { id: string; title: string; detail: string; at: string | null; href: string; source: string; kind: "action" | "problem" | "info" };

const list = async (url: string): Promise<any[]> => {
  const r = await get(url);
  return r.ok && Array.isArray(r.data) ? r.data : [];
};

/** Every current alert, newest first. `checked` lists sources that could be read. */
export async function loadAlerts(withMarketing: boolean): Promise<{ alerts: Alert[]; unavailable: string[] }> {
  const unavailable: string[] = [];
  const guard = async (name: string, f: () => Promise<Alert[]>) => {
    try { return await f(); } catch { unavailable.push(name); return []; }
  };
  const parts = await Promise.all([
    guard("AI approvals", async () => {
      const r = await get("/api/v1/ai/recommendations?status=pending");
      if (!r.ok) throw new Error();
      return (r.data as any[]).map((x) => ({
        id: `rec:${x.id}`, kind: "action" as const, source: x.agent_key.replace(/_/g, " "), at: x.created_at ?? null, href: "/admin/ai/approvals",
        title: `${x.action_type.replace(/_/g, " ")} — waiting for approval`, detail: `${x.reasoning.slice(0, 180)}${x.tier ? ` · tier ${x.tier}` : ""}`,
      }));
    }),
    guard("Message delivery", async () => {
      const r = await get("/api/v1/notifications/notifications?status=failed&hours=24&limit=200");
      if (!r.ok) throw new Error();
      const byChannel: Record<string, any[]> = {};
      for (const n of r.data as any[]) (byChannel[n.channel] ||= []).push(n);
      return Object.entries(byChannel).map(([ch, rows]) => ({
        id: `fail:${ch}:${rows[0].id}`, kind: "problem" as const, source: "notification service", at: rows[0].created_at, href: "/admin/messaging",
        title: `${rows.length} ${ch} message${rows.length > 1 ? "s" : ""} failed in the last 24h`, detail: rows[0].error_message || rows[0].provider_status || "",
      }));
    }),
    guard("Integrations", async () => (await list("/api/v1/rbac/integrations")).filter((i) => i.configured && i.test_status === "failed").map((i) => ({
      id: `integ:${i.provider}:${i.tested_at}`, kind: "problem" as const, source: "integrations", at: i.tested_at, href: "/admin/integrations",
      title: `${i.label || i.provider} connection test failed`, detail: i.test_detail || "Check the credentials under Integrations.",
    }))),
    guard("Sign-in lockouts", async () => (await list("/api/v1/rbac/users")).filter((u) => u.locked).map((u) => ({
      id: `lock:${u.id}:${u.locked_until}`, kind: "action" as const, source: "auth service", at: null, href: "/admin/access/users",
      title: `${u.display_name} is locked out`, detail: `${u.failed_login_attempts} failed sign-in attempts. Unlock from Users & roles if this was them.`,
    }))),
    guard("Permission overrides", async () => (await list("/api/v1/rbac/permission-overrides")).filter((o) => o.risk_flagged && !o.revoked).map((o) => ({
      id: `ovr:${o.id}`, kind: "action" as const, source: "compliance agent", at: null, href: "/admin/access/overrides",
      title: `Risky ${o.effect} override: ${o.permission_key}`, detail: o.reason || "Flagged for review.",
    }))),
    withMarketing ? guard("Content review", async () => {
      const held = await list("/api/v1/marketing/content?status=held_for_review");
      return held.length ? [{ id: `held:${held.map((d) => d.id).sort().join(",").slice(0, 200)}`, kind: "action" as const, source: "marketing guardrails", at: null, href: "/marketing/review",
        title: `${held.length} marketing draft${held.length > 1 ? "s" : ""} held for review`, detail: held[0].guardrail_findings?.[0] || "Guardrails flagged the copy." }] : [];
    }) : Promise.resolve([]),
  ]);
  const alerts = parts.flat().sort((a, b) => (b.at || "").localeCompare(a.at || ""));
  return { alerts, unavailable };
}

const key = (userId: string) => `voyqi.alerts.read.${userId}`;
export function readIds(userId: string): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(key(userId)) || "[]")); } catch { return new Set(); }
}
export function markRead(userId: string, ids: string[]) {
  try {
    const next = new Set([...readIds(userId), ...ids]);
    localStorage.setItem(key(userId), JSON.stringify([...next].slice(-500)));
    window.dispatchEvent(new Event("voyqi-alerts-read"));
  } catch { /* storage unavailable: everything stays unread */ }
}
