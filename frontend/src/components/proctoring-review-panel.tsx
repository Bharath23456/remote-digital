"use client";

import { CheckCircle2, Eye, LoaderCircle, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { csrfFetch } from "@/lib/api";

type Row = Record<string, unknown> & { id: string; status?: string; version?: number };
type Evidence = { id: string; reason: string; url: string; mime_type: string; captured_from: string; captured_to: string; sha256: string };

const label = (value: unknown) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const date = (value: unknown) => value ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(String(value))) : "-";

export function ProctoringReviewPanel({ sessions, reviews, onReload }: { sessions: Row[]; reviews: Row[]; onReload: () => Promise<void> }) {
  const [selected, setSelected] = useState<Row | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function open(review: Row) {
    setSelected(review); setEvidence([]); setNote(String(review.decision_note || "")); setError(""); setBusy(true);
    try {
      const response = await csrfFetch(`/api/v1/phase4/remote-security/reviews/${review.id}/evidence`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Evidence could not be loaded");
      setEvidence(body.items || []);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Evidence could not be loaded"); }
    finally { setBusy(false); }
  }

  async function decide(status: "reviewing" | "cleared" | "escalated") {
    if (!selected) return;
    setBusy(true); setError("");
    try {
      const response = await csrfFetch(`/api/v1/phase4/remote-security/reviews/${selected.id}/action`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: selected.version, status, note }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Review decision could not be saved");
      setSelected(null); await onReload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Review decision could not be saved"); }
    finally { setBusy(false); }
  }

  return <div className="security-review-stack">
    <section className="panel"><header className="panel-header"><div><h3 className="panel-title">Secure sessions</h3><p className="panel-subtitle">Live posture, pause state and heartbeat continuity</p></div></header><div className="table-wrap"><table><thead><tr><th>Assignment</th><th>State</th><th>Violations</th><th>Pause reason</th><th>Last heartbeat</th></tr></thead><tbody>{sessions.length ? sessions.map((item) => <tr key={item.id}><td>{String(item.assignment_id).slice(0, 12)}</td><td><span className={`status-pill ${item.status}`}>{label(item.status)}</span></td><td>{String(item.violation_count || 0)}</td><td>{label(item.pause_reason) || "-"}</td><td>{date(item.last_heartbeat_at)}</td></tr>) : <tr><td colSpan={5}><div className="compact-empty">No secure evaluation sessions yet.</div></td></tr>}</tbody></table></div></section>
    <section className="panel"><header className="panel-header"><div><h3 className="panel-title">Human review queue</h3><p className="panel-subtitle">Critical events require an authorized decision; detections are never treated as automatic guilt.</p></div></header><div className="table-wrap"><table><thead><tr><th>Event</th><th>Severity</th><th>Evidence</th><th>Created</th><th>Status</th><th>Action</th></tr></thead><tbody>{reviews.length ? reviews.map((item) => <tr key={item.id}><td>{label(item.category)}</td><td><span className={`status-pill ${item.severity}`}>{label(item.severity)}</span></td><td>{String(item.evidence_count || 0)} clip(s)</td><td>{date(item.created_at)}</td><td><span className={`status-pill ${item.status}`}>{label(item.status)}</span></td><td><button className="text-button" onClick={() => open(item)}><Eye />Review</button></td></tr>) : <tr><td colSpan={6}><div className="compact-empty">No incidents require review.</div></td></tr>}</tbody></table></div></section>
    {selected && <div className="modal-backdrop"><div className="modal-panel proctoring-modal" role="dialog" aria-modal="true" aria-labelledby="proctoring-title"><header className="modal-header"><div><h2 id="proctoring-title">Security event review</h2><p>{label(selected.category)} · {label(selected.severity)}</p></div><button className="icon-button" title="Close" onClick={() => setSelected(null)}><X /></button></header><div className="proctoring-body">{busy && !evidence.length ? <div className="empty-state"><LoaderCircle className="spin" /></div> : evidence.length ? <div className="evidence-grid">{evidence.map((item) => <figure key={item.id}><video controls preload="metadata" src={item.url} /><figcaption><strong>{label(item.reason)}</strong><span>{date(item.captured_from)} · SHA-256 {item.sha256.slice(0, 12)}</span></figcaption></figure>)}</div> : <div className="evidence-empty"><ShieldAlert /><strong>No verified clip available</strong><span>Use the event record and session posture to make the decision.</span></div>}<label className="field"><span>Review note</span><textarea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Record the evidence considered and decision rationale" /></label>{error && <div className="form-error">{error}</div>}</div><footer className="modal-footer"><button className="secondary-button" disabled={busy} onClick={() => decide("reviewing")}><Eye />Keep reviewing</button><button className="secondary-button" disabled={busy || note.trim().length < 8} onClick={() => decide("cleared")}><CheckCircle2 />Clear</button><button className="danger-button" disabled={busy || note.trim().length < 8} onClick={() => decide("escalated")}><ShieldAlert />Escalate</button></footer></div></div>}
  </div>;
}
