"use client";

import { Check, CloudUpload, FileQuestion, LoaderCircle, ScanText, ShieldAlert } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Readiness = { ready: boolean; question_paper: boolean; reference_answers: number; configured_questions: number; total_questions: number; missing_question_guides: number };
type ReferenceAsset = { id: string; kind: string; slot: number; file_name: string; mime_type: string; byte_size: number; asset_version: number };
type QuestionGuide = { id: string; number: string; sub_question: string; max_marks: number; question_text: string; evaluation_guidance: string; version: number };
type Paper = { id: string; code: string; title: string; reference_pack: { readiness: Readiness; assets: ReferenceAsset[]; questions: QuestionGuide[] } };
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

  async function uploadReference(file: File, kind: "question_paper" | "reference_answer", slot: number) {
    if (!paper) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const intent = await api("/api/v1/ai-evaluation/reference-uploads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paper_id: paper.id, kind, slot, file_name: file.name, content_type: file.type || "application/pdf", maximum_bytes: file.size }) });
      const uploaded = await csrfFetch(intent.upload_url, { method: "PUT", headers: intent.headers, body: file });
      if (!uploaded.ok) throw new Error("Reference file upload failed");
      await api(`/api/v1/ai-evaluation/reference-uploads/${intent.id}/finalize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: intent.version }) });
      setNotice(`${kind === "question_paper" ? "Question paper" : `Reference answer ${slot}`} uploaded`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Reference upload failed"); }
    finally { setSaving(false); }
  }

  async function extractQuestions() {
    if (!paper) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await api(`/api/v1/ai-evaluation/papers/${paper.id}/extract-questions`, { method: "POST" });
      setNotice(`${result.matched} question${result.matched === 1 ? "" : "s"} extracted for review`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Question extraction failed"); }
    finally { setSaving(false); }
  }

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
    <header className="ai-reference-heading"><div><h3>ADMIEZO AI reference material</h3><p>Add the question paper and three evaluated reference answers for this subject.</p></div><div className={`readiness-label ${readiness.ready ? "ready" : "attention"}`}>{readiness.ready ? <Check /> : <ShieldAlert />}{readiness.ready ? "Ready" : "Setup incomplete"}</div></header>
    {notice && <div className="success-banner"><Check />{notice}</div>}
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="ai-reference-grid">
      <ReferenceUpload label="Question paper" icon={FileQuestion} kind="question_paper" slot={1} asset={paper.reference_pack.assets.find((item) => item.kind === "question_paper")} busy={saving} onUpload={uploadReference} />
      {[1, 2, 3].map((slot) => <ReferenceUpload label={`Reference answer ${slot}`} icon={CloudUpload} kind="reference_answer" slot={slot} asset={paper.reference_pack.assets.find((item) => item.kind === "reference_answer" && item.slot === slot)} busy={saving} onUpload={uploadReference} key={slot} />)}
    </div>
    <div className="panel-footer split"><span>{readiness.configured_questions}/{readiness.total_questions} questions configured</span><button className="secondary-button" disabled={saving || !readiness.question_paper} onClick={extractQuestions}><ScanText />Extract question text</button></div>
    <div className="ai-question-list">{paper.reference_pack.questions.map((question) => <form className="ai-question-row" onSubmit={(event) => saveQuestion(event, question)} key={question.id}><div className="ai-question-number"><strong>{question.number}{question.sub_question ? ` (${question.sub_question})` : ""}</strong><span>{question.max_marks} marks</span></div><label className="field"><span>Question text</span><textarea name="question_text" defaultValue={question.question_text} rows={3} required /></label><label className="field"><span>Evaluation guidance</span><textarea name="evaluation_guidance" defaultValue={question.evaluation_guidance} rows={3} placeholder="Key concepts, acceptable methods, partial-credit guidance" /></label><button className="icon-button" title={`Save question ${question.number}`} aria-label={`Save question ${question.number}`} disabled={saving}><Check /></button></form>)}</div>
    {!paper.reference_pack.questions.length && <div className="empty-state">Add question numbers and maximum marks to this paper first.</div>}
  </div>;
}

function ReferenceUpload({ label, icon: Icon, kind, slot, asset, busy, onUpload }: { label: string; icon: typeof FileQuestion; kind: "question_paper" | "reference_answer"; slot: number; asset?: ReferenceAsset; busy: boolean; onUpload: (file: File, kind: "question_paper" | "reference_answer", slot: number) => Promise<void> }) {
  return <label className={`ai-reference-slot ${asset ? "complete" : ""}`}><Icon /><span><strong>{label}</strong><small>{asset ? `${asset.file_name} · v${asset.asset_version}` : "PDF, PNG, JPEG or WebP"}</small></span><input type="file" accept="application/pdf,image/png,image/jpeg,image/webp" disabled={busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void onUpload(file, kind, slot); event.currentTarget.value = ""; }} />{asset ? <Check /> : <CloudUpload />}</label>;
}
