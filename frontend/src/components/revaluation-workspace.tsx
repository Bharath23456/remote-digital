"use client";

import { Check, RefreshCw, RotateCcw, ShieldCheck, UserRoundCheck, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { csrfFetch } from "@/lib/api";

type RequestRow = {
  id: string; script_id: string; script: string; paper: string; applicant_reference: string; reason: string;
  status: string; original_mark: number; revaluated_mark: number | null; difference: number | null;
  effective_mark: number | null; decision: string; decision_note: string; evaluator_id: string | null;
  evaluator: string | null; assignment_id: string | null; result_id: string | null; created_at: string;
  assigned_at: string | null; completed_at: string | null; decided_at: string | null; version: number;
};
type Catalog = {
  requests: RequestRow[];
  eligible_scripts: { id: string; script: string; paper: string; final_mark: number }[];
  evaluators: { id: string; code: string; name: string }[];
};

const empty: Catalog = { requests: [], eligible_scripts: [], evaluators: [] };
const title = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

async function api(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Revaluation operation failed");
  return body;
}

export function RevaluationWorkspace() {
  const [catalog, setCatalog] = useState<Catalog>(empty);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [assigning, setAssigning] = useState<RequestRow | null>(null);
  const [deciding, setDeciding] = useState<RequestRow | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setCatalog(await api("/api/v1/revaluation/catalog"));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Revaluation data could not be loaded");
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  async function action(path: string, body: object, message: string) {
    setBusy(true); setError(""); setNotice("");
    try {
      await api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setNotice(message); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Operation failed"); }
    finally { setBusy(false); }
  }

  const pending = useMemo(() => catalog.requests.filter((item) => ["requested", "approved"].includes(item.status)).length, [catalog.requests]);
  const active = useMemo(() => catalog.requests.filter((item) => item.status === "assigned").length, [catalog.requests]);
  const decisions = useMemo(() => catalog.requests.filter((item) => item.status === "decision_pending").length, [catalog.requests]);

  return <div className="config-workspace">
    <div className="receiving-summary">
      <div><RotateCcw /><span>Total requests</span><strong>{catalog.requests.length}</strong></div>
      <div><ShieldCheck /><span>Awaiting control</span><strong>{pending}</strong></div>
      <div><UserRoundCheck /><span>With evaluators</span><strong>{active}</strong></div>
      <div><Check /><span>Decision pending</span><strong>{decisions}</strong></div>
    </div>

    {error && <div className="form-error" role="alert">{error}</div>}
    {notice && <div className="success-note">{notice}</div>}

    <section className="panel">
      <header className="panel-header"><div><h2 className="panel-title">Revaluation control</h2><p className="panel-subtitle">Post-finalisation requests with independent evaluator assignment and auditable final decision</p></div><div className="row-actions"><button className="secondary-button" onClick={() => void load()} disabled={busy}><RefreshCw className={busy ? "spin" : ""} />Refresh</button><button className="primary-button" onClick={() => setCreateOpen(true)} disabled={!catalog.eligible_scripts.length}>New request</button></div></header>
      <div className="table-wrap"><table><thead><tr><th>Script</th><th>Reference</th><th>Original</th><th>Revaluated</th><th>Evaluator</th><th>Status</th><th>Control</th></tr></thead><tbody>
        {catalog.requests.map((item) => <tr key={item.id}>
          <td><strong>{item.script}</strong><small className="table-subtext">{item.paper}</small></td>
          <td>{item.applicant_reference || "—"}</td>
          <td>{item.original_mark}</td>
          <td>{item.revaluated_mark ?? "—"}{item.difference !== null && <small className="table-subtext">Δ {item.difference > 0 ? "+" : ""}{item.difference}</small>}</td>
          <td>{item.evaluator || "—"}</td>
          <td><span className={`status-pill ${item.status}`}>{title(item.status)}</span></td>
          <td><div className="row-actions">
            {item.status === "requested" && <><button title="Approve" onClick={() => void action(`/api/v1/revaluation/requests/${item.id}/approve`, { version: item.version }, "Revaluation request approved")}><Check /></button><button title="Reject" onClick={() => { const note = window.prompt("Rejection reason"); if (note) void action(`/api/v1/revaluation/requests/${item.id}/reject`, { version: item.version, note }, "Revaluation request rejected"); }}><X /></button></>}
            {item.status === "approved" && <button title="Assign independent evaluator" onClick={() => setAssigning(item)}><UserRoundCheck /></button>}
            {item.status === "assigned" && <button title="Capture completed evaluator result" onClick={() => void action(`/api/v1/revaluation/requests/${item.id}/sync`, { version: item.version }, "Revaluation result captured")}><RefreshCw /></button>}
            {item.status === "decision_pending" && <button title="Record final decision" onClick={() => setDeciding(item)}><ShieldCheck /></button>}
          </div></td>
        </tr>)}
      </tbody></table></div>
      {!catalog.requests.length && <div className="empty-state">No revaluation requests have been recorded yet.</div>}
    </section>

    {createOpen && <CreateModal catalog={catalog} onClose={() => setCreateOpen(false)} onSaved={async () => { setCreateOpen(false); setNotice("Revaluation request created"); await load(); }} />}
    {assigning && <AssignModal item={assigning} evaluators={catalog.evaluators} onClose={() => setAssigning(null)} onSaved={async () => { setAssigning(null); setNotice("Independent evaluator assigned"); await load(); }} />}
    {deciding && <DecisionModal item={deciding} onClose={() => setDeciding(null)} onSaved={async () => { setDeciding(null); setNotice("Revaluation decision recorded"); await load(); }} />}
  </div>;
}

function CreateModal({ catalog, onClose, onSaved }: { catalog: Catalog; onClose: () => void; onSaved: () => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget);
    try {
      await api("/api/v1/revaluation/requests", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ script_id: form.get("script_id"), applicant_reference: form.get("reference"), reason: form.get("reason") }) });
      await onSaved();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not create request"); }
    finally { setBusy(false); }
  }
  return <div className="modal-backdrop"><form className="modal-panel" onSubmit={submit}><header className="modal-header"><div><h2>New revaluation request</h2><p>Create a governed request only for a script with a locked final mark.</p></div><button type="button" className="icon-button" onClick={onClose}><X /></button></header><div className="form-grid">
    <label className="field full-field"><span>Finalised script</span><select name="script_id" required>{catalog.eligible_scripts.map((item) => <option key={item.id} value={item.id}>{item.script} · {item.paper} · final mark {item.final_mark}</option>)}</select></label>
    <label className="field full-field"><span>Applicant / application reference</span><input name="reference" maxLength={120} placeholder="Optional student/application reference" /></label>
    <label className="field full-field"><span>Reason</span><textarea name="reason" minLength={8} required placeholder="Reason for requesting revaluation" /></label>
    {error && <div className="form-error full-field">{error}</div>}
  </div><footer className="modal-footer"><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Creating..." : "Create request"}</button></footer></form></div>;
}

function AssignModal({ item, evaluators, onClose, onSaved }: { item: RequestRow; evaluators: Catalog["evaluators"]; onClose: () => void; onSaved: () => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget);
    try {
      await api(`/api/v1/revaluation/requests/${item.id}/assign`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: item.version, evaluator_id: form.get("evaluator_id"), due_in_hours: Number(form.get("due")) }) }); await onSaved();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not assign evaluator"); }
    finally { setBusy(false); }
  }
  return <div className="modal-backdrop"><form className="modal-panel" onSubmit={submit}><header className="modal-header"><div><h2>Assign revaluation</h2><p>{item.script} · original mark {item.original_mark}</p></div><button type="button" className="icon-button" onClick={onClose}><X /></button></header><div className="form-grid">
    <label className="field full-field"><span>Independent evaluator</span><select name="evaluator_id" required><option value="">Select evaluator</option>{evaluators.map((evaluator) => <option value={evaluator.id} key={evaluator.id}>{evaluator.code} · {evaluator.name}</option>)}</select></label>
    <label className="field"><span>Due in hours</span><input name="due" type="number" min="1" max="720" defaultValue="120" required /></label>
    <div className="field full-field"><small>The backend blocks any evaluator who already performed an earlier valuation of this script.</small></div>
    {error && <div className="form-error full-field">{error}</div>}
  </div><footer className="modal-footer"><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Assigning..." : "Assign evaluator"}</button></footer></form></div>;
}

function DecisionModal({ item, onClose, onSaved }: { item: RequestRow; onClose: () => void; onSaved: () => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget);
    try {
      await api(`/api/v1/revaluation/requests/${item.id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: item.version, effective_mark: Number(form.get("mark")), note: form.get("note") }) }); await onSaved();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not record decision"); }
    finally { setBusy(false); }
  }
  return <div className="modal-backdrop"><form className="modal-panel" onSubmit={submit}><header className="modal-header"><div><h2>Final revaluation decision</h2><p>{item.script} · original {item.original_mark} · revaluated {item.revaluated_mark}</p></div><button type="button" className="icon-button" onClick={onClose}><X /></button></header><div className="form-grid">
    <label className="field"><span>Effective mark</span><input name="mark" type="number" step="0.01" min="0" defaultValue={item.revaluated_mark ?? item.original_mark} required /></label>
    <label className="field full-field"><span>Decision note</span><textarea name="note" minLength={5} required placeholder="Policy/rationale for confirming or revising the final mark" /></label>
    {error && <div className="form-error full-field">{error}</div>}
  </div><footer className="modal-footer"><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Recording..." : "Record decision"}</button></footer></form></div>;
}