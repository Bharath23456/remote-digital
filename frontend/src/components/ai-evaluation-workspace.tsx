"use client";

import { Bot, BrainCircuit, Check, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Governance = { mode: "assistive" | "autonomous"; confidence_threshold: number; model_name: string };
type Readiness = { ready: boolean; guide_only_ready: boolean; question_paper: boolean; reference_answers: number; configured_questions: number; total_questions: number; missing_question_guides: number; missing_evaluation_guidance: number };
type Paper = { id: string; code: string; title: string; subject: string; status: string; ready_scripts: number; reference_pack: { readiness: Readiness } };
type Analysis = { id: string; assignment_id: string; script: string; paper_id: string; paper: string; trigger: string; status: string; model_name: string; effective_confidence: number | null; error_message: string; evaluator: string; backup_evaluator: string | null; assignment_status: string; created_at: string; started_at: string | null; completed_at: string | null };
type Catalog = { provider: { provider: string; configured: boolean; valid: boolean; available: boolean; message: string }; governance: Governance; papers: Paper[]; analyses: Analysis[] };

const emptyCatalog: Catalog = { provider: { provider: "admiezo_ai", configured: false, valid: false, available: false, message: "ADMIEZO AI Assistant is unavailable" }, governance: { mode: "assistive", confidence_threshold: 85, model_name: "admiezo-ai-v1" }, papers: [], analyses: [] };
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const analysisReason = (item: Analysis) => item.status === "low_confidence" && item.error_message.includes("request failed (429)")
  ? "AI provider quota exceeded (429). Manual evaluation is assigned."
  : item.error_message;
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
      setPaperId((current) => body.papers.some((item) => item.id === current) ? current : body.papers[0]?.id || "");
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "AI evaluation workspace could not be loaded"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { const initial = window.setTimeout(() => void load(), 0); const poll = window.setInterval(() => void load(), 5000); return () => { window.clearTimeout(initial); window.clearInterval(poll); }; }, [load]);
  const paper = useMemo(() => catalog.papers.find((item) => item.id === paperId) || catalog.papers[0], [catalog.papers, paperId]);
  const analyses = catalog.analyses.filter((item) => !paper || item.paper_id === paper.id);
  const latestAutonomousResult = analyses.find((item) => item.trigger === "autonomous" && item.status !== "queued" && item.status !== "running");
  const quotaLimited = latestAutonomousResult?.error_message.includes("request failed (429)") ?? false;
  const blockers = useMemo(() => {
    if (!paper) return [];
    const readiness = paper.reference_pack.readiness;
    const items: string[] = [];
    if (!catalog.provider.available) items.push(catalog.provider.message);
    if (catalog.governance.mode === "autonomous" && paper.status !== "frozen") items.push("Freeze this paper configuration");
    if (readiness.total_questions === 0) items.push("Configure questions and maximum marks");
    else if (readiness.missing_question_guides > 0) items.push(`Complete question text for ${readiness.missing_question_guides} ${readiness.missing_question_guides === 1 ? "question" : "questions"}`);
    if (readiness.missing_evaluation_guidance > 0) items.push(`Complete evaluation guidance for ${readiness.missing_evaluation_guidance} ${readiness.missing_evaluation_guidance === 1 ? "question" : "questions"}`);
    if (catalog.governance.mode === "autonomous" && paper.ready_scripts === 0) items.push("No unassigned stored scripts are ready for this paper");
    return items;
  }, [catalog.governance.mode, catalog.provider.available, catalog.provider.message, paper]);

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
    <div className="workspace-toolbar"><label className="field ai-paper-select"><span>Paper and subject</span><select value={paper?.id || ""} onChange={(event) => setPaperId(event.target.value)}>{catalog.papers.map((item) => <option value={item.id} key={item.id}>{item.code} · {item.subject} · {item.title}</option>)}</select></label><div className="header-actions"><div className="provider-state ready"><BrainCircuit /><div><strong>{catalog.governance.mode === "autonomous" ? "Autonomous evaluation" : "Assisted evaluation"}</strong><span>{catalog.governance.confidence_threshold}% human fallback threshold</span></div></div><div className={`provider-state ${quotaLimited ? "" : "ready"}`}><Check /><div><strong>{quotaLimited ? "AI provider quota reached on latest run" : "ADMIEZO AI Assistant model verified"}</strong><span>{quotaLimited ? "The provider rejected generation (429); manual fallback is active." : catalog.provider.message}</span></div></div></div></div>
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && <div className="form-error">{error}</div>}
    {!paper ? <section className="panel"><div className="empty-state">Create a paper before running AI evaluation.</div></section> : <section className="panel"><header className="panel-header"><div><h2 className="panel-title">AI analysis progress</h2><p className="panel-subtitle">{catalog.governance.mode === "autonomous" ? `AI evaluates in the background. Work below ${catalog.governance.confidence_threshold}% is automatically routed to a human evaluator.` : "Evaluators can request AI analysis from the Evaluation desk after reference material is configured under Exam configuration."}</p></div>{catalog.governance.mode === "autonomous" && <button className="primary-button" title={blockers.length ? blockers.join(". ") : `Queue ${paper.ready_scripts} ready scripts for this paper`} disabled={saving || blockers.length > 0} onClick={assignAll}><Bot />Assign {paper.ready_scripts || "ready"} {paper.ready_scripts === 1 ? "script" : "scripts"} to AI</button>}</header>{catalog.governance.mode === "autonomous" && paper.ready_scripts === 0 && <p className="panel-subtitle">No unassigned stored scripts are available for this paper. Upload and store a new masked script to enable AI assignment.</p>}<div className="table-wrap"><table><thead><tr><th>Anonymous script</th><th>Mode</th><th>Evaluator</th><th>Confidence</th><th>Started</th><th>Status</th></tr></thead><tbody>{analyses.map((item) => <tr key={item.id}><td><strong>{item.script}</strong><br/><small>{item.paper}</small></td><td>{titleCase(item.trigger)}</td><td>{item.evaluator}{item.status === "low_confidence" && item.backup_evaluator && <><br/><small>Backup: {item.backup_evaluator}</small></>}</td><td>{item.effective_confidence === null ? "—" : `${item.effective_confidence}%`}</td><td>{new Date(item.created_at).toLocaleString("en-IN")}</td><td className="ai-analysis-status"><span className={`status-pill ${item.status}`}>{item.status === "low_confidence" ? "Assigned for manual evaluation" : titleCase(item.status)}</span>{item.status === "low_confidence" && <small className="analysis-state">Human assignment: {titleCase(item.assignment_status)}</small>}{item.error_message && <small className={item.status === "low_confidence" ? "analysis-note" : "analysis-error"}>{analysisReason(item)}</small>}</td></tr>)}</tbody></table></div>{!analyses.length && <div className="ai-queue-empty"><Sparkles /><strong>{blockers.length ? "AI setup is not ready" : catalog.governance.mode === "autonomous" ? "No AI analyses yet" : "No assistance requested yet"}</strong><p>{blockers.length ? "Complete these items before using AI evaluation:" : catalog.governance.mode === "autonomous" ? `${paper.ready_scripts} ready ${paper.ready_scripts === 1 ? "script is" : "scripts are"} available for autonomous evaluation.` : "Evaluators can request an analysis while reviewing an assigned script."}</p>{blockers.length > 0 && <ul>{blockers.map((item) => <li key={item}>{item}</li>)}</ul>}</div>}</section>}
  </div>;
}
