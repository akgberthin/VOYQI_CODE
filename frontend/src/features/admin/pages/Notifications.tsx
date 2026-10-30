"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useSession } from "@/lib/session";
import { Badge, Empty, Loading, PageHead, fmtTime, useLoad } from "@/lib/ui";
import { loadAlerts, markRead, readIds } from "../alerts";

export default function Notifications() {
  const { me } = useSession();
  const { data, loading, reload } = useLoad(() => loadAlerts(!!me?.access.marketing));
  const [read, setRead] = useState<Set<string>>(new Set());
  const [only, setOnly] = useState(false);
  useEffect(() => {
    const sync = () => me && setRead(readIds(me.user_id));
    sync();
    window.addEventListener("voyqi-alerts-read", sync);
    return () => window.removeEventListener("voyqi-alerts-read", sync);
  }, [me]);
  const alerts = data?.alerts || [];
  const unread = alerts.filter((a) => !read.has(a.id));
  const shown = only ? unread : alerts;
  return (
    <>
      <PageHead title="Operator notifications" text="What needs a person right now — AI recommendations waiting for approval, failed deliveries, failing integrations, locked accounts, risky overrides. Read live from the services.">
        <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> Unread only</label>
        <button className="ghost" onClick={reload}>Refresh</button>
        <button disabled={!unread.length} onClick={() => me && markRead(me.user_id, alerts.map((a) => a.id))}>Mark all read</button>
      </PageHead>
      {(data?.unavailable || []).length > 0 && <div className="notice warn mb">Couldn&apos;t check: {data!.unavailable.join(", ")}.</div>}
      {loading && !data ? <Loading /> : !shown.length ? <Empty>{only && alerts.length ? "All caught up." : "Nothing needs attention."}</Empty> : (
        <div className="card">
          {shown.map((a, i) => {
            const isRead = read.has(a.id);
            return (
              <div key={a.id} className="row" style={{ alignItems: "flex-start", gap: 13, padding: "12px 0", borderTop: i ? "1px solid var(--line)" : undefined, opacity: isRead ? 0.55 : 1 }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", marginTop: 6, flexShrink: 0, background: isRead ? "var(--muted)" : a.kind === "problem" ? "var(--bad)" : "var(--gold)" }} />
                <div style={{ flex: 1 }}>
                  <div className="row between"><b>{a.title}</b>{a.kind === "problem" ? <Badge kind="bad">problem</Badge> : <Badge kind="gold">action</Badge>}</div>
                  {a.detail && <div className="muted" style={{ marginTop: 2 }}>{a.detail}</div>}
                  <div className="row" style={{ marginTop: 6, fontSize: 12 }}>
                    <span className="muted">{a.at ? fmtTime(a.at) : "now"} · {a.source}</span>
                    <Link href={a.href} onClick={() => me && markRead(me.user_id, [a.id])}>Open →</Link>
                    {!isRead && <button className="sm ghost" onClick={() => me && markRead(me.user_id, [a.id])}>Mark read</button>}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
