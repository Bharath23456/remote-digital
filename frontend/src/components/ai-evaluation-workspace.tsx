"use client";

import { Bot, BrainCircuit, Check, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Governance = { mode: "assistive" | "autonomous"; confidence_threshold: number; model_name: string };
type Paper = { id: string; code: string; title: string; subject: string; status: string; reference_pack: { readiness: { ready: boolean } } };
type Analysis = { id: string; assignment_id: string; script: string; paper_id: string; paper: string; trigger: string; status: string; model_name: string; effective_confidence: number | null; error_message: string; evaluator: string; created_at: string; started_at: string | null; completed_at: string | null };
type Catalog = { provider: { provider: string; configured: boolean; valid: boolean; available: boolean; message: string }; governance: Governance; papers: Paper[]; analyses: Analysis[] };

const emptyCatalog: Catalog = { provider: { provider: "admiezo_ai", configured: false, valid: false, available: false, message: "ADMIEZO AI Assistant is unavailable" }, governance: { mode: "assistive", confidence_threshold: 85, model_name: "admiezo-ai-v1" }, papers: [], analyses: [] };
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
async function api(path: string, options?: RequestInit) { const response = await csrfFetch(path, options); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || "AI evaluation operation failed"); return body; }

export function AIEvaluationWorkspace() {
  const [catalog, setCatalog] = useState<Catalog>(emptyCatalog);
  const [paperId, setPaperId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const body = await api("/api/v1/ai-evaluation/catalog") as Catalog;
      setCatalog(body);
      setPaperId((current) => current || body.papers[0]?.id || "");
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "AI evaluation workspace could not be loaded"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); const poll = window.setInterval(() => void load(), 5000); return () => { window.clearTimeout(initial); window.clearInterval(poll); }; }, [load]);
  const paper = useMemo(() => catalog.papers.find((item) => item.id === paperId) || catalog.papers[0], [catalog.papers, paperId]);
  const analyses = catalog.analyses.filter((item) => !paper || item.paper_id === paper.id);

  async function assignAll() {
    if (!paper) return;
    setSaving(true); setError("");
    try {
      const result = await api("/api/v1/ai-evaluation/assignments", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paper_id: paper.id, maximum_scripts: 5000 }) });
      setNotice(`${result.queued} script${result.queued === 1 ? "" : "s"} queued for autonomous evaluation`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Scripts could not be assigned to AI"); }
    finally { setSaving(false); }
  }

  if (loading) return <div className="empty-state"><RefreshCw className="spin" />Loading AI evaluation controls</div>;
  return <div className="config-workspace ai-evaluation-workspace">
    <div className="workspace-toolbar"><label className="field ai-paper-select"><span>Paper and subject</span><select value={paper?.id || ""} onChange={(event) => setPaperId(event.target.value)}>{catalog.papers.map((item) => <option value={item.id} key={item.id}>{item.code} · {item.subject} · {item.title}</option>)}</select></label><div className="header-actions"><div className="provider-state ready"><BrainCircuit /><div><strong>{catalog.governance.mode === "autonomous" ? "Autonomous evaluation" : "Assisted evaluation"}</strong><span>{catalog.governance.confidence_threshold}% human fallback threshold</span></div></div><div className="provider-state ready"><Check /><div><strong>ADMIEZO AI Assistant verified</strong><span>{catalog.provider.message}</span></div></div></div></div>
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && <div className="form-error">{error}</div>}
    {!paper ? <section className="panel"><div className="empty-state">Create a paper before running AI evaluation.</div></section> : <section className="panel"><header className="panel-header"><div><h2 className="panel-title">AI analysis progress</h2><p className="panel-subtitle">{catalog.governance.mode === "autonomous" ? `AI evaluates in the background. Work below ${catalog.governance.confidence_threshold}% is automatically routed to a human evaluator.` : "Evaluators can request AI analysis from the Evaluation desk after reference material is configured under Exam configuration."}</p></div>{catalog.governance.mode === "autonomous" && <button className="primary-button" disabled={saving || !paper.reference_pack.readiness.ready || !catalog.provider.available || paper.status !== "frozen"} onClick={assignAll}><Bot />Assign ready scripts to AI</button>}</header><div className="table-wrap"><table><thead><tr><th>Anonymous script</th><th>Mode</th><th>Evaluator</th><th>Confidence</th><th>Started</th><th>Status</th></tr></thead><tbody>{analyses.map((item) => <tr key={item.id}><td><strong>{item.script}</strong><br/><small>{item.paper}</small></td><td>{titleCase(item.trigger)}</td><td>{item.evaluator}</td><td>{item.effective_confidence === null ? "—" : `${item.effective_confidence}%`}</td><td>{new Date(item.created_at).toLocaleString("en-IN")}</td><td><span className={`status-pill ${item.status}`}>{titleCase(item.status)}</span>{item.error_message && <small className="analysis-error">{item.error_message}</small>}</td></tr>)}</tbody></table></div>{!analyses.length && <div className="empty-state"><Sparkles />No AI analyses for this paper yet.</div>}</section>}
  </div>;
}
