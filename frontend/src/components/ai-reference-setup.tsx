"use client";

import { Check, LoaderCircle } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Readiness = { configured_questions: number; total_questions: number };
type QuestionGuide = { id: string; number: string; sub_question: string; max_marks: number; question_text: string; evaluation_guidance: string; version: number };
type Paper = { id: string; code: string; title: string; reference_pack: { readiness: Readiness; questions: QuestionGuide[] } };
type Catalog = { provider: { available: boolean }; papers: Paper[] };

async function api(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "AI reference operation failed");
  return body;
}

export function AIReferenceSetup({ paperId }: { paperId: string }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const body = await api("/api/v1/ai-evaluation/catalog") as Catalog;
      setCatalog(body);
      setUnavailable(false);
      setError("");
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "AI reference setup could not be loaded";
      if (/disabled|missing|invalid|unavailable/i.test(message)) setUnavailable(true);
      else setError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  const paper = useMemo(() => catalog?.papers.find((item) => item.id === paperId), [catalog, paperId]);

  async function saveQuestion(event: FormEvent<HTMLFormElement>, question: QuestionGuide) {
    event.preventDefault();
    if (!paper) return;
    setSaving(true); setError(""); setNotice("");
    const data = new FormData(event.currentTarget);
    try {
      await api(`/api/v1/ai-evaluation/question-guides/${question.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paper_id: paper.id, question_id: question.id, question_text: data.get("question_text"), evaluation_guidance: data.get("evaluation_guidance"), max_marks: question.max_marks, version: question.version }) });
      setNotice(`Question ${question.number}${question.sub_question ? ` (${question.sub_question})` : ""} saved`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Question guidance could not be saved"); }
    finally { setSaving(false); }
  }

  if (unavailable) return null;
  if (loading) return <div className="ai-reference-loading"><LoaderCircle className="spin" />Loading ADMIEZO AI reference setup</div>;
  if (!paper || !catalog?.provider.available) return null;

  const readiness = paper.reference_pack.readiness;
  return <div className="ai-reference-setup">
    {notice && <div className="success-banner"><Check />{notice}</div>}
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="panel-footer split"><span>{readiness.configured_questions}/{readiness.total_questions} questions configured</span></div>
    <div className="ai-question-list">{paper.reference_pack.questions.map((question) => <form className="ai-question-row" onSubmit={(event) => saveQuestion(event, question)} key={question.id}><div className="ai-question-number"><strong>{question.number}{question.sub_question ? ` (${question.sub_question})` : ""}</strong><span>{question.max_marks} marks</span></div><label className="field"><span>Question text</span><textarea name="question_text" defaultValue={question.question_text} rows={3} required /></label><label className="field"><span>Evaluation guidance</span><textarea name="evaluation_guidance" defaultValue={question.evaluation_guidance} rows={3} placeholder="Key concepts, acceptable methods, partial-credit guidance" /></label><button className="icon-button" title={`Save question ${question.number}`} aria-label={`Save question ${question.number}`} disabled={saving}><Check /></button></form>)}</div>
    {!paper.reference_pack.questions.length && <div className="empty-state">Add question numbers and maximum marks to this paper first.</div>}
  </div>;
}
