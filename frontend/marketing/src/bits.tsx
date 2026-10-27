import { useSession } from "@shared/session";
import { StatusBadge, useAction } from "@shared/ui";
import { CHANNEL_ICON, CHANNEL_LABEL, mk, type Draft } from "./mk";

export function Ch({ c }: { c: string }) {
  return <span className={`ch ch-${c}`}>{CHANNEL_ICON[c]} {CHANNEL_LABEL[c] || c}</span>;
}

/** A content draft with its guardrail result and the review buttons a
    held draft needs. */
export function DraftCard({ d, onChange, actions }: { d: Draft; onChange?: () => void; actions?: React.ReactNode }) {
  const run = useAction();
  const { me } = useSession();
  return (
    <div className="card">
      <div className="row between">
        <div className="row"><Ch c={d.channel} /><StatusBadge value={d.status} />{d.created_by.startsWith("ai") || d.created_by.includes("agent") ? <span className="badge ai">AI draft</span> : null}</div>
        <span className="muted mono" style={{ fontSize: 11 }}>{d.id.slice(0, 8)}</span>
      </div>
      {d.subject_line && <div className="mt"><b>{d.subject_line}</b></div>}
      <div className="mt" style={{ whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{d.body_text}</div>
      {d.guardrail_findings?.length > 0 && <ul className="findings">{d.guardrail_findings.map((f) => <li key={f}>{f}</li>)}</ul>}
      {d.reviewed_by && <div className="muted mt">Reviewed by {d.reviewed_by}</div>}
      <div className="row mt">
        {d.status === "held_for_review" && <>
          <button className="sm ok" onClick={() => run("Approved", () => mk.approveDraft(d.id, me?.email || "marketing")).then(onChange)}>Approve anyway</button>
          <button className="sm danger" onClick={() => run("Rejected", () => mk.rejectDraft(d.id, me?.email || "marketing")).then(onChange)}>Reject</button>
        </>}
        {actions}
      </div>
    </div>
  );
}
