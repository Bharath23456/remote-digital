"use client";

import { Check, ChevronLeft, ChevronRight, Eye, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Script = { id: string; script_code: string; state: string; page_count: number; intake_mode: string };
type Job = { id: string; script_id: string; script: string; page_count: number; status: string; failure_reason: string; version: number; intake_mode: string };

export function GuidedMaskingWorkspace() {
  const [scripts, setScripts] = useState<Script[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Job | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try {
      const response = await csrfFetch("/api/v1/anonymisation/catalog");
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Masking queue could not be loaded");
      setScripts((body.scripts || []).filter((item: Script) => item.intake_mode !== "legacy"));
      setJobs((body.jobs || []).filter((item: Job) => item.intake_mode !== "legacy"));
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Masking queue could not be loaded"); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  const latestJobs = useMemo(() => {
    const byScript = new Map<string, Job>();
    for (const job of jobs) if (!byScript.has(job.script_id)) byScript.set(job.script_id, job);
    return byScript;
  }, [jobs]);

  async function retry(script: Script) {
    setBusy(script.id); setError(""); setNotice("");
    try {
      const response = await csrfFetch(`/api/v1/anonymisation/scripts/${script.id}/auto-mask`, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Masking could not finish");
      setNotice(`${script.script_code} is masked and ready in Script repository.`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Masking could not finish"); }
    finally { setBusy(""); }
  }

  async function returnForRemasking(job: Job) {
    setBusy(job.id); setError(""); setNotice("");
    try {
      const response = await csrfFetch(`/api/v1/anonymisation/masking-jobs/${job.id}/return-for-remasking`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: job.version, notes: reason, passed: false }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Could not return the script for remasking");
      setSelected(null); setReason(""); setNotice(`${job.script} returned for remasking.`);
      await load();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not return the script for remasking"); }
    finally { setBusy(""); }
  }

  return <div className="config-workspace">
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && <div className="form-error" role="alert">{error}</div>}
    <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Masking queue</h2><p className="panel-subtitle">Only anonymous script codes appear here.</p></div></header><div className="table-wrap"><table><thead><tr><th>Script</th><th>Pages</th><th>State</th><th>Masking</th><th>Actions</th></tr></thead><tbody>{scripts.map((script) => { const job = latestJobs.get(script.id); const done = script.state === "stored" && job?.status === "verified"; return <tr key={script.id}><td><strong>{script.script_code}</strong></td><td>{script.page_count}</td><td>{script.state.replaceAll("_", " ")}</td><td>{done ? "Complete" : job?.status === "failed" ? job.failure_reason || "Needs retry" : job?.status || "Pending"}</td><td><div className="row-actions">{job && ["detected", "applied", "verified"].includes(job.status) && <button className="icon-button" title="View masked pages" aria-label={`View masked pages for ${script.script_code}`} onClick={() => { setSelected(job); setPageNumber(1); }}><Eye /></button>}{!done && ["scanned", "validated", "masked"].includes(script.state) && <button className="secondary-button" disabled={!!busy} onClick={() => void retry(script)}><RotateCcw />{busy === script.id ? "Masking..." : "Mask now"}</button>}</div></td></tr>; })}</tbody></table></div>{!scripts.length && <div className="empty-state">No guided scripts scanned yet.</div>}</section>
    {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><div className="modal-panel mask-review-modal" role="dialog" aria-modal="true" aria-label="Masked pages" onClick={(event) => event.stopPropagation()}><header className="modal-header"><div><h2>{selected.script} · Masked pages</h2><p>Page {pageNumber} of {selected.page_count}</p></div><button className="icon-button" title="Close" onClick={() => setSelected(null)}><X /></button></header><div className="mask-review-body"><div className="mask-review-image"><img key={`${selected.id}-${pageNumber}`} src={`/api/v1/anonymisation/masking-jobs/${selected.id}/preview/${pageNumber}?version=${selected.version}`} alt={`Masked page ${pageNumber} of ${selected.script}`} /></div><div className="mask-review-toolbar"><button className="icon-button" title="Previous page" disabled={pageNumber <= 1} onClick={() => setPageNumber(pageNumber - 1)}><ChevronLeft /></button><span>Page {pageNumber} / {selected.page_count}</span><button className="icon-button" title="Next page" disabled={pageNumber >= selected.page_count} onClick={() => setPageNumber(pageNumber + 1)}><ChevronRight /></button></div>{selected.status === "verified" && <div className="mask-review-footer"><label className="field"><span>Reason to remask</span><textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} placeholder="Describe the visible identity or masking error" /></label><button className="secondary-button danger-text" disabled={!!busy || reason.trim().length < 10} onClick={() => void returnForRemasking(selected)}>Return for remasking</button></div>}</div></div></div>}
  </div>;
}
