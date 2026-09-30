"use client";

import { Check, ChevronDown, ChevronRight, ClipboardList, FilePlus2, History, LoaderCircle, Pencil, Plus, ShieldAlert, Undo2, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DynamicFields, readDynamicFields, useDynamicFields } from "@/components/dynamic-fields";
import { AIReferenceSetup } from "@/components/ai-reference-setup";
import { csrfFetch } from "@/lib/api";
import { safeRandomUUID } from "@/lib/crypto-compat";

type Catalog = Record<string, Record<string, unknown>[]>;
type EntityKey = "academic_years" | "regulations" | "terms" | "sessions" | "events" | "programmes" | "courses" | "subjects" | "centres" | "papers";
type Field = { name: string; label: string; type?: string; required?: boolean; options?: { value: string; label: string }[] };
type Readiness = { decision: string; ready: boolean; session: { name: string; status: string } | null; paper_count?: number; issues: string[]; critical_alerts: string[] };

const entityLabels: Record<EntityKey, string> = {
  academic_years: "Academic years", regulations: "Regulations", terms: "Terms",
  sessions: "Exam sessions", events: "Evaluation events", programmes: "Programmes", courses: "Courses",
  subjects: "Subjects", centres: "Evaluation centres", papers: "Papers",
};

const endpoints: Record<EntityKey, string> = {
  academic_years: "academic-years", regulations: "regulations", terms: "terms",
  sessions: "sessions", events: "events", programmes: "programmes", courses: "courses",
  subjects: "subjects", centres: "centres", papers: "papers",
};

const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const id = (row: Record<string, unknown>) => String(row.id);
const markInputValue = (value: unknown) => {
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : "";
};
const localDateTime = (value: unknown) => {
  if (!value) return undefined;
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return undefined;
  const part = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${part(date.getMonth() + 1)}-${part(date.getDate())}T${part(date.getHours())}:${part(date.getMinutes())}`;
};

async function apiRequest(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "The operation could not be completed");
  return body;
}

export function ConfigurationWorkspace() {
  const sessionFields = useDynamicFields("exam_session");
  const paperCustomFields = useDynamicFields("paper");
  const [catalog, setCatalog] = useState<Catalog>({});
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [active, setActive] = useState<EntityKey>("papers");
  const [modal, setModal] = useState<"entity" | "master" | "master-status" | "master-history" | "calendar-exception" | "question" | "questions" | "edit" | "change" | "history" | null>(null);
  const [selectedPaper, setSelectedPaper] = useState<Record<string, unknown> | null>(null);
  const [selectedQuestion, setSelectedQuestion] = useState<Record<string, unknown> | null>(null);
  const [selectedMaster, setSelectedMaster] = useState<Record<string, unknown> | null>(null);
  const [activateMaster, setActivateMaster] = useState(false);
  const [selectedImpact, setSelectedImpact] = useState<Record<string, unknown> | null>(null);
  const [historyRows, setHistoryRows] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [eventSessionId, setEventSessionId] = useState("");
  const [expandedPaperId, setExpandedPaperId] = useState<string | null>(null);
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);
  const createKey = useRef("");

  const load = useCallback(async () => {
    try {
      const [nextCatalog, nextReadiness, identity] = await Promise.all([apiRequest("/api/v1/configuration/catalog"), apiRequest("/api/v1/configuration/readiness"), apiRequest("/api/v1/auth/me")]);
      setCatalog(nextCatalog); setReadiness(nextReadiness); setCurrentUserId(identity.user.id); setError("");
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Configuration could not be loaded"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  const options = useCallback((key: EntityKey, primary: string, secondary?: string) =>
    (catalog[key] || []).map((row) => ({ value: id(row), label: secondary ? `${row[primary]} · ${row[secondary]}` : String(row[primary]) })), [catalog]);

  const fields = useMemo<Record<EntityKey, Field[]>>(() => ({
    academic_years: [{ name: "label", label: "Academic year", required: true }, { name: "starts_on", label: "Starts on", type: "date", required: true }, { name: "ends_on", label: "Ends on", type: "date", required: true }],
    regulations: [{ name: "code", label: "Regulation code", required: true }, { name: "title", label: "Title", required: true }, { name: "effective_from", label: "Effective from", type: "date", required: true }, { name: "effective_to", label: "Effective to", type: "date" }],
    terms: [{ name: "academic_year_id", label: "Academic year", type: "select", required: true, options: options("academic_years", "label") }, { name: "name", label: "Term name", required: true }, { name: "sequence", label: "Sequence", type: "number", required: true }, { name: "starts_on", label: "Starts on", type: "date", required: true }, { name: "ends_on", label: "Ends on", type: "date", required: true }],
    sessions: [{ name: "academic_year_id", label: "Academic year", type: "select", required: true, options: options("academic_years", "label") }, { name: "name", label: "Session name", required: true }, { name: "term_id", label: "Term", type: "select", required: true, options: options("terms", "name", "academic_year") }, { name: "evaluation_starts_at", label: "Evaluation starts", type: "datetime-local", required: true }, { name: "evaluation_ends_at", label: "Evaluation ends", type: "datetime-local", required: true }],
    events: [{ name: "session_id", label: "Exam session", type: "select", required: true, options: options("sessions", "name", "term") }, { name: "name", label: "Event name", required: true }, { name: "starts_at", label: "Starts at", type: "datetime-local", required: true }, { name: "ends_at", label: "Ends at", type: "datetime-local", required: true }, { name: "evaluation_centre_ids", label: "Evaluation centres", type: "multiselect", required: true, options: options("centres", "code", "name") }],
    programmes: [{ name: "code", label: "Programme code", required: true }, { name: "name", label: "Programme name", required: true }, { name: "regulation_id", label: "Regulation", type: "select", required: true, options: options("regulations", "code", "title") }],
    courses: [{ name: "programme_id", label: "Programme", type: "select", required: true, options: options("programmes", "code", "name") }, { name: "regulation_id", label: "Regulation", type: "select", required: true, options: options("regulations", "code", "title") }, { name: "code", label: "Course code", required: true }, { name: "name", label: "Course name", required: true }, { name: "duration_terms", label: "Duration in terms", type: "number", required: true }],
    subjects: [{ name: "programme_id", label: "Programme", type: "select", required: true, options: options("programmes", "code", "name") }, { name: "course_id", label: "Course", type: "select", required: true, options: options("courses", "code", "name") }, { name: "session_ids", label: "Available exam sessions", type: "multiselect", required: true, options: options("sessions", "name", "term") }, { name: "related_subject_ids", label: "Related subjects", type: "multiselect", options: options("subjects", "code", "name") }, { name: "code", label: "Subject code", required: true }, { name: "name", label: "Subject name", required: true }, { name: "semester", label: "Semester", type: "number", required: true }],
    centres: [{ name: "code", label: "Centre code", required: true }, { name: "name", label: "Centre name", required: true }, { name: "address", label: "Address" }, { name: "network_cidrs", label: "Approved networks (comma separated)" }],
    papers: [{ name: "session_id", label: "Exam session", type: "select", required: true, options: options("sessions", "name", "term") }, { name: "subject_id", label: "Subject", type: "select", required: true, options: options("subjects", "code", "name") }, { name: "code", label: "Paper code", required: true }, { name: "title", label: "Paper title", required: true }, { name: "max_marks", label: "Maximum marks", type: "number", required: true }, { name: "pass_marks", label: "Passing marks", type: "number", required: true }, { name: "valuation_rounds", label: "Valuation rounds", type: "number", required: true }, { name: "second_valuation_mark_threshold", label: "Round 2 if score exceeds (one-round papers only)", type: "number" }, { name: "discrepancy_threshold", label: "Difference threshold between rounds", type: "number", required: true }, { name: "moderation_required", label: "Moderation required", type: "checkbox" }, { name: "critical_change", label: "Require dual approval", type: "checkbox" }],
  }), [options]);

  async function createEntity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = {};
    for (const field of fields[active]) {
      if (field.type === "checkbox") payload[field.name] = data.get(field.name) === "on";
      else if (field.type === "multiselect") payload[field.name] = data.getAll(field.name);
      else if (field.type === "number") payload[field.name] = Number(data.get(field.name));
      else if (field.type === "datetime-local") payload[field.name] = new Date(String(data.get(field.name))).toISOString();
      else if (field.name === "network_cidrs") payload[field.name] = String(data.get(field.name) || "").split(",").map((item) => item.trim()).filter(Boolean);
      else if (data.get(field.name)) payload[field.name] = data.get(field.name);
    }
    if (active === "papers") {
      payload.rules = { critical_change: payload.critical_change, revaluation: true, second_valuation_mark_threshold: data.get("second_valuation_mark_threshold") || null };
      delete payload.critical_change;
      delete payload.second_valuation_mark_threshold;
    }
    if (active === "sessions") payload.custom_fields = readDynamicFields(data, sessionFields);
    if (active === "papers") payload.custom_fields = readDynamicFields(data, paperCustomFields);
    if (active === "events") {
      const session = (catalog.sessions || []).find((item) => id(item) === String(payload.session_id));
      const starts = new Date(String(payload.starts_at));
      const ends = new Date(String(payload.ends_at));
      if (session && (starts < new Date(String(session.evaluation_starts_at)) || ends > new Date(String(session.evaluation_ends_at)))) {
        setError(`Event must be within ${new Date(String(session.evaluation_starts_at)).toLocaleString("en-IN")} and ${new Date(String(session.evaluation_ends_at)).toLocaleString("en-IN")}.`);
        setSaving(false);
        return;
      }
    }
    try {
      const created = await apiRequest(`/api/v1/configuration/${endpoints[active]}`, { method: "POST", headers: { "Content-Type": "application/json", ...(["sessions", "events"].includes(active) ? { "Idempotency-Key": createKey.current } : {}) }, body: JSON.stringify(payload) });
      if (active === "papers" && created.id) setExpandedPaperId(String(created.id));
      setModal(null); setNotice(`${entityLabels[active]} record created`); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Record could not be created"); }
    finally { setSaving(false); }
  }

  async function saveQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selectedPaper) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    const values = { number: data.get("number"), sub_question: data.get("sub_question"), max_marks: data.get("max_marks"), question_type: data.get("question_type"), position: Number(data.get("position")), required: data.get("required") === "on" };
    try {
      const path = selectedQuestion ? `/api/v1/configuration/papers/${selectedPaper.id}/questions/${selectedQuestion.id}` : `/api/v1/configuration/papers/${selectedPaper.id}/questions`;
      const payload = selectedQuestion ? { ...values, version: selectedPaper.version } : values;
      await apiRequest(path, { method: selectedQuestion ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setModal(null); setSelectedQuestion(null); setNotice(selectedQuestion ? "Question updated and paper readiness recalculated" : "Question added and paper readiness recalculated"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Question could not be added"); }
    finally { setSaving(false); }
  }

  async function deleteQuestion(paper: Record<string, unknown>, question: Record<string, unknown>) {
    if (!window.confirm(`Remove question ${String(question.number)}?`)) return;
    setSaving(true); setError("");
    try {
      await apiRequest(`/api/v1/configuration/papers/${paper.id}/questions/${question.id}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: paper.version }) });
      setModal(null); setNotice("Question removed and paper readiness recalculated"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Question could not be removed"); }
    finally { setSaving(false); }
  }

  async function paperAction(paper: Record<string, unknown>, action: "submit" | "approve" | "freeze") {
    setSaving(true); setError("");
    try {
      await apiRequest(`/api/v1/configuration/papers/${paper.id}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: paper.version, note: `${titleCase(action)} from administration console` }) });
      setNotice(`Paper ${action} completed`); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : `Paper could not be ${action}d`); }
    finally { setSaving(false); }
  }

  async function editPaper(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selectedPaper) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    const payload = {
      version: selectedPaper.version,
      title: data.get("title"),
      max_marks: data.get("max_marks"),
      pass_marks: data.get("pass_marks"),
      valuation_rounds: Number(data.get("valuation_rounds")),
      discrepancy_threshold: data.get("discrepancy_threshold"),
      moderation_required: data.get("moderation_required") === "on",
      rules: { ...(selectedPaper.rules as object || {}), critical_change: data.get("critical_change") === "on", second_valuation_mark_threshold: data.get("second_valuation_mark_threshold") || null },
    };
    try {
      await apiRequest(`/api/v1/configuration/papers/${selectedPaper.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setModal(null); setNotice("Draft paper configuration updated"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Paper could not be updated"); }
    finally { setSaving(false); }
  }

  async function requestGovernedChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selectedPaper) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget); const kind = String(data.get("kind"));
    const changes = kind === "emergency_update" ? {
      title: data.get("title"), max_marks: data.get("max_marks"), pass_marks: data.get("pass_marks"),
      valuation_rounds: Number(data.get("valuation_rounds")), discrepancy_threshold: data.get("discrepancy_threshold"),
      moderation_required: data.get("moderation_required") === "on",
      rules: { ...(selectedPaper.rules as object || {}), second_valuation_mark_threshold: data.get("second_valuation_mark_threshold") || null },
    } as Record<string, unknown> : {};
    const questionText = String(data.get("questions_json") || "").trim();
    if (kind === "emergency_update" && questionText) {
      try { changes.questions = JSON.parse(questionText); }
      catch { setError("Questions must be a valid JSON array"); setSaving(false); return; }
    }
    const target = Number(data.get("target_revision_version"));
    try {
      await apiRequest(`/api/v1/configuration/papers/${selectedPaper.id}/changes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: selectedPaper.version, kind, reason: data.get("reason"), changes, target_revision_version: kind === "rollback" && target ? target : null }) });
      setModal(null); setNotice("Governed change sent for two-person approval"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Change request could not be created"); }
    finally { setSaving(false); }
  }

  async function openHistory(paper: Record<string, unknown>) {
    setSelectedPaper(paper); setError("");
    try { setHistoryRows(await apiRequest(`/api/v1/configuration/papers/${paper.id}/history`)); setModal("history"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Configuration history could not be loaded"); }
  }

  async function openGovernedChange(paper: Record<string, unknown>) {
    setSelectedPaper(paper); setError("");
    try { setSelectedImpact(await apiRequest(`/api/v1/configuration/papers/${paper.id}/impact`)); setModal("change"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Change impact could not be loaded"); }
  }

  async function decideChange(change: Record<string, unknown>, decision: "approved" | "rejected") {
    setSaving(true); setError("");
    try {
      await apiRequest(`/api/v1/configuration/changes/${change.id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: change.version, decision, note: `${titleCase(decision)} in configuration console` }) });
      setNotice(`Configuration change ${decision}`); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Change decision could not be recorded"); }
    finally { setSaving(false); }
  }

  const masterFields = fields[active].filter((field) => !["academic_year_id", "term_id", "programme_id", "regulation_id", "course_id", "session_id"].includes(field.name));

  async function updateMaster(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selectedMaster) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget); const changes: Record<string, unknown> = {};
    for (const field of masterFields) {
      if (field.type === "checkbox") changes[field.name] = data.get(field.name) === "on";
      else if (field.type === "multiselect") changes[field.name] = data.getAll(field.name);
      else if (field.name === "network_cidrs") changes[field.name] = String(data.get(field.name) || "").split(",").map((value) => value.trim()).filter(Boolean);
      else if (field.type === "number") changes[field.name] = Number(data.get(field.name));
      else if (field.type === "datetime-local") changes[field.name] = new Date(String(data.get(field.name))).toISOString();
      else changes[field.name] = data.get(field.name) || null;
    }
    try {
      await apiRequest(`/api/v1/configuration/masters/${endpoints[active]}/${selectedMaster.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: selectedMaster.version, changes, reason: data.get("reason") }) });
      setModal(null); setNotice("Configuration updated with an audit revision"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Configuration could not be updated"); }
    finally { setSaving(false); }
  }

  async function setMasterActive(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selectedMaster) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try { await apiRequest(`/api/v1/configuration/masters/${endpoints[active]}/${selectedMaster.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: selectedMaster.version, changes: { is_active: activateMaster }, reason: data.get("reason") }) }); setModal(null); setNotice(activateMaster ? "Record activated" : "Record retired"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Status could not be changed"); }
    finally { setSaving(false); }
  }

  async function transition(row: Record<string, unknown>) {
    const next: Record<string, string> = { draft: "approval", approval: "ready", ready: "active", active: "closed" };
    const target = next[String(row.status)]; if (!target) return; setSaving(true); setError("");
    try { await apiRequest(`/api/v1/configuration/sessions/${row.id}/transition`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: row.version, target, reason: `Moved to ${target} by administrator` }) }); setNotice(`Session moved to ${titleCase(target)}`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Session transition failed"); }
    finally { setSaving(false); }
  }

  async function openMasterHistory(row: Record<string, unknown>) {
    setSelectedMaster(row); setError("");
    try { setHistoryRows(await apiRequest(`/api/v1/configuration/masters/${endpoints[active]}/${row.id}/history`)); setModal("master-history"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "History could not be loaded"); }
  }

  async function requestCalendarException(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError(""); const data = new FormData(event.currentTarget);
    try {
      await apiRequest("/api/v1/configuration/calendar-exceptions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entity: active === "terms" ? "term" : "academic_year", academic_year_id: data.get("academic_year_id") || null, target_id: data.get("target_id") || null, starts_on: data.get("starts_on"), ends_on: data.get("ends_on"), reason: data.get("reason") }) });
      setModal(null); setNotice("Calendar exception sent for independent approval"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Exception request could not be submitted"); }
    finally { setSaving(false); }
  }

  async function decideCalendarException(row: Record<string, unknown>, approve: boolean) {
    setSaving(true); setError("");
    try { await apiRequest(`/api/v1/configuration/calendar-exceptions/${row.id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve }) }); setNotice(approve ? "Calendar exception approved" : "Calendar exception rejected"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Exception decision failed"); }
    finally { setSaving(false); }
  }

  const rows = catalog[active] || [];
  const columns = rows.length ? Object.keys(rows[0]).filter((key) => !["id", "questions", "rules", "network_cidrs", "evaluation_centre_ids", "readiness", "session_id", "subject_id", "academic_year_id", "programme_id", "regulation_id", "regulation_record_id", "term_record_id", "submitted_by_id", "frozen_at", "effective_from"].includes(key)).slice(0, 6) : [];
  const visibleColumns = active === "events" ? ["name", "session", "centres", "starts_at", "ends_at", "is_active"] : active === "terms" ? ["name", "academic_year", "sequence", "starts_on", "ends_on", "is_active"] : columns;
  const pendingChanges = (catalog.change_requests || []).filter((item) => item.status === "pending");
  const eventSession = (catalog.sessions || []).find((item) => id(item) === eventSessionId);
  const subjectLabel = (subjectId: unknown) => {
    const subject = (catalog.subjects || []).find((item) => id(item) === String(subjectId));
    return subject ? `${String(subject.code)} · ${String(subject.name)}` : "—";
  };

  return <div className="config-workspace">
    {readiness && <><div className={`config-readiness ${readiness.ready ? "ready" : "attention"}`}><div><strong>{readiness.ready ? "GO-LIVE" : "NOT READY"}</strong><span>{readiness.session?.name || "No examination session"}</span></div><div><span>Papers</span><strong>{readiness.paper_count || 0}</strong></div><div><span>Open issues</span><strong>{readiness.issues.length + readiness.critical_alerts.length}</strong></div></div>{!readiness.ready && <div className="config-blockers" aria-label="Go-live blockers">{[...readiness.critical_alerts, ...readiness.issues].map((issue, index) => <button key={`${issue}-${index}`} onClick={() => { const paper = (catalog.papers || []).find((row) => issue.startsWith(`${row.code}:`)); if (paper) { setActive("papers"); setExpandedPaperId(id(paper)); } else setActive(issue.toLowerCase().includes("centre") ? "centres" : issue.toLowerCase().includes("event") ? "events" : issue.toLowerCase().includes("session") ? "sessions" : "papers"); window.setTimeout(() => document.getElementById(paper ? `paper-${id(paper)}` : "config-records")?.scrollIntoView({ behavior: "smooth", block: "start" }), 0); }}><ShieldAlert />{issue}<ChevronRight /></button>)}</div>}</>}
    <div className="workspace-toolbar"><div className="entity-tabs" role="tablist">{(Object.keys(entityLabels) as EntityKey[]).map((key) => <button className={active === key ? "active" : ""} onClick={() => { setActive(key); setNotice(""); }} key={key}>{entityLabels[key]}</button>)}</div><div className="header-actions">{["academic_years", "terms"].includes(active) && <button className="secondary-button" onClick={() => { setModal("calendar-exception"); setError(""); }}><ShieldAlert />Overlap exception</button>}<button className="primary-button" onClick={() => { createKey.current = safeRandomUUID(); setModal("entity"); setError(""); }}><Plus />Add {entityLabels[active].replace(/s$/, "")}</button></div></div>
    {notice && <div className="success-banner"><Check />{notice}</div>}
    {error && !modal && <div className="form-error" role="alert">{error}</div>}
    <section className="panel" id="config-records">
      <header className="panel-header"><div><h2 className="panel-title">{entityLabels[active]}</h2><p className="panel-subtitle">{rows.length} configured records</p></div></header>
      {loading ? <div className="empty-state"><LoaderCircle className="spin" /></div> : rows.length ? active === "papers" ? <div className="paper-record-list">{rows.map((row) => <PaperRecord
        key={id(row)}
        paper={row}
        currentUserId={currentUserId}
        expanded={expandedPaperId === id(row)}
        saving={saving}
        onToggle={() => setExpandedPaperId((current) => current === id(row) ? null : id(row))}
        onAddQuestion={() => { setSelectedPaper(row); setSelectedQuestion(null); setModal("question"); setError(""); }}
        onViewQuestions={() => { setSelectedPaper(row); setModal("questions"); setError(""); }}
        onEditQuestion={(question) => { setSelectedPaper(row); setSelectedQuestion(question); setModal("question"); setError(""); }}
        onDeleteQuestion={(question) => void deleteQuestion(row, question)}
        onEdit={() => { setSelectedPaper(row); setModal("edit"); setError(""); }}
        onHistory={() => void openHistory(row)}
        onGovernedChange={() => void openGovernedChange(row)}
        onAction={(action) => void paperAction(row, action)}
      />)}</div> : <div className="table-wrap"><table><thead><tr>{visibleColumns.map((column) => <th key={column}>{titleCase(column)}</th>)}<th>Actions</th></tr></thead><tbody>{rows.map((row) => <tr key={id(row)}>{visibleColumns.map((column) => <td key={column}>{column === "status" ? <span className={`status-pill ${row[column]}`}>{titleCase(String(row[column]))}</span> : typeof row[column] === "boolean" ? (row[column] ? "Active" : "Inactive") : String(row[column] ?? "—")}</td>)}<td><div className="row-actions"><button title="Edit record" aria-label={`Edit ${String(row.name || row.code || row.label)}`} onClick={() => { setSelectedMaster(row); setModal("master"); setError(""); }}><Pencil /></button><button title="Revision history" aria-label="Revision history" onClick={() => void openMasterHistory(row)}><History /></button><button title={row.is_active === false ? "Activate record" : "Retire record"} aria-label={row.is_active === false ? "Activate record" : "Retire record"} onClick={() => { setSelectedMaster(row); setActivateMaster(row.is_active === false); setModal("master-status"); setError(""); }} disabled={saving}><X /></button>{active === "sessions" && row.status !== "closed" && <button title={`Advance session from ${String(row.status)}`} onClick={() => void transition(row)} disabled={saving}><ChevronRight /></button>}</div></td></tr>)}</tbody></table></div> : <div className="empty-state"><div><FilePlus2 /><strong>No {entityLabels[active].toLowerCase()} yet</strong><p>Create the first record to continue configuration.</p></div></div>}
    </section>
    {["academic_years", "terms"].includes(active) && (catalog.calendar_exceptions || []).some((row) => row.entity === (active === "terms" ? "term" : "academic_year")) && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Calendar exceptions</h2><p className="panel-subtitle">Exact-date approvals are consumed when a matching record is saved</p></div></header><div className="table-wrap"><table><thead><tr><th>Window</th><th>Reason</th><th>Status</th><th>Decision</th></tr></thead><tbody>{(catalog.calendar_exceptions || []).filter((row) => row.entity === (active === "terms" ? "term" : "academic_year")).map((row) => <tr key={id(row)}><td>{String(row.starts_on)} to {String(row.ends_on)}</td><td>{String(row.reason)}</td><td><span className={`status-pill ${String(row.status)}`}>{titleCase(String(row.status))}</span></td><td>{row.status === "pending" && <div className="row-actions"><button disabled={saving} onClick={() => void decideCalendarException(row, true)}>Approve</button><button disabled={saving} onClick={() => void decideCalendarException(row, false)}>Reject</button></div>}</td></tr>)}</tbody></table></div></section>}
    {active === "papers" && pendingChanges.length > 0 && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Governed changes</h2><p className="panel-subtitle">Emergency and rollback requests require two independent approvals</p></div></header><div className="table-wrap"><table><thead><tr><th>Paper</th><th>Change</th><th>Impact</th><th>Approvals</th><th>Decision</th></tr></thead><tbody>{pendingChanges.map((change) => <tr key={String(change.id)}><td><strong>{String(change.paper)}</strong><br /><small>{String(change.reason)}</small></td><td>{titleCase(String(change.kind))}</td><td>{String((change.impact as { scripts?: number }).scripts || 0)} scripts</td><td>{String(change.approval_count)} / {String(change.required_approvals)}</td><td><div className="row-actions"><button disabled={saving} onClick={() => decideChange(change, "approved")}>Approve</button><button disabled={saving} onClick={() => decideChange(change, "rejected")}>Reject</button></div></td></tr>)}</tbody></table></div></section>}
    {modal === "entity" && <div className="modal-backdrop" role="presentation"><div className="modal-panel" role="dialog" aria-modal="true" aria-labelledby="entity-modal-title"><header className="modal-header"><div><h2 id="entity-modal-title">Add {entityLabels[active].replace(/s$/, "")}</h2><p>This record becomes part of the active university configuration.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={createEntity} onChange={() => { createKey.current = safeRandomUUID(); }}><div className="form-grid">{fields[active].map((field) => <label className={`field ${field.type === "checkbox" ? "check-field" : ""}`} key={field.name}>{field.type === "checkbox" ? <><input name={field.name} type="checkbox" /><span>{field.label}</span></> : <><span>{field.label}</span>{field.type === "select" || field.type === "multiselect" ? <select name={field.name} required={field.required} defaultValue={field.type === "multiselect" ? undefined : ""} multiple={field.type === "multiselect"} size={field.type === "multiselect" ? Math.min(6, Math.max(3, field.options?.length || 3)) : undefined} onChange={field.name === "session_id" && active === "events" ? (event) => setEventSessionId(event.target.value) : undefined}><option value="" disabled={field.type !== "multiselect"}>Select {field.label.toLowerCase()}</option>{field.options?.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select> : <input name={field.name} type={field.type || "text"} required={field.required} min={field.type === "number" ? (field.name === "duration_terms" ? 1 : field.name === "max_marks" ? 0.01 : 0) : field.type === "datetime-local" && active === "events" ? localDateTime(eventSession?.evaluation_starts_at) : undefined} max={field.type === "datetime-local" && active === "events" ? localDateTime(eventSession?.evaluation_ends_at) : undefined} step={field.type === "number" ? "0.01" : undefined} />}</>}</label>)}{active === "events" && eventSession && <div className="form-guidance full-field"><strong>Session evaluation window</strong><span>{new Date(String(eventSession.evaluation_starts_at)).toLocaleString("en-IN")} to {new Date(String(eventSession.evaluation_ends_at)).toLocaleString("en-IN")}</span></div>}{active === "sessions" && <DynamicFields fields={sessionFields} />}{active === "papers" && <DynamicFields fields={paperCustomFields} />}</div>{error && <div className="form-error" role="alert">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>{saving ? "Saving…" : "Create record"}<ChevronRight /></button></footer></form></div></div>}
    {modal === "question" && selectedPaper && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{selectedQuestion ? "Edit question" : "Add question"}</h2><p>{String(selectedPaper.code)} · {String(selectedPaper.title)}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={saveQuestion}><div className="form-grid"><label className="field"><span>Question number</span><input name="number" defaultValue={String(selectedQuestion?.number || "")} required /></label><label className="field"><span>Sub-question</span><input name="sub_question" maxLength={8} defaultValue={String(selectedQuestion?.sub_question || "")} /></label><label className="field"><span>Maximum marks</span><input name="max_marks" type="number" min="0" step="0.01" defaultValue={String(selectedQuestion?.max_marks || "")} required /></label><label className="field"><span>Question type</span><select name="question_type" defaultValue={String(selectedQuestion?.question_type || "descriptive")}><option value="descriptive">Descriptive</option><option value="objective">Objective</option><option value="practical">Practical</option><option value="oral">Oral</option><option value="other">Other</option></select></label><label className="field"><span>Position</span><input name="position" type="number" min="1" defaultValue={String(selectedQuestion?.position || (Array.isArray(selectedPaper.questions) ? selectedPaper.questions.length + 1 : 1))} required /></label><label className="field check-field"><input name="required" type="checkbox" defaultChecked={selectedQuestion ? Boolean(selectedQuestion.required) : true} /><span>Required question</span></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>{saving ? "Saving…" : selectedQuestion ? "Save question" : "Add question"}</button></footer></form></div></div>}
    {modal === "edit" && selectedPaper && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Edit draft paper</h2><p>{String(selectedPaper.code)} · version {String(selectedPaper.version)}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={editPaper}><PaperFields paper={selectedPaper} />{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>{saving ? "Saving…" : "Save changes"}</button></footer></form></div></div>}
    {modal === "change" && selectedPaper && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Request governed change</h2><p>{String(selectedPaper.code)} · two independent approvals required</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={requestGovernedChange}>{selectedImpact && <div className="impact-warning"><ShieldAlert /><div><strong>Active configuration impact</strong><span>{String(selectedImpact.scripts || 0)} scripts · {String(selectedImpact.active_assignments || 0)} active · {String(selectedImpact.submitted_assignments || 0)} submitted</span></div></div>}<div className="form-grid"><label className="field"><span>Change type</span><select name="kind" defaultValue="emergency_update"><option value="emergency_update">Emergency update</option><option value="rollback">Rollback to revision</option></select></label><label className="field"><span>Rollback revision</span><input name="target_revision_version" type="number" min="1" placeholder="Only for rollback" /></label><label className="field full-field"><span>Reason</span><input name="reason" minLength={12} required /></label></div><PaperFields paper={selectedPaper} /><label className="field full-field"><span>Replacement questions (JSON array, optional)</span><textarea name="questions_json" rows={5} placeholder='[{"number":"Q1","max_marks":"50","required":true}]' /></label>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>{saving ? "Submitting…" : "Submit for approval"}<ChevronRight /></button></footer></form></div></div>}
    {modal === "questions" && selectedPaper && <div className="modal-backdrop"><div className="modal-panel question-marks-modal" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Questions and marks</h2><p>{String(selectedPaper.code)} · {String(selectedPaper.title)}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><QuestionMarksRecord paper={selectedPaper} subject={subjectLabel(selectedPaper.subject_id)} saving={saving} editable={selectedPaper.status === "draft"} onAddQuestion={() => { setSelectedQuestion(null); setModal("question"); setError(""); }} onEditQuestion={(question) => { setSelectedQuestion(question); setModal("question"); setError(""); }} onDeleteQuestion={(question) => void deleteQuestion(selectedPaper, question)} /></div></div>}
    {modal === "history" && selectedPaper && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Configuration history</h2><p>{String(selectedPaper.code)} · immutable revision record</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><div className="history-list">{historyRows.map((item) => <div className="history-item" key={String(item.version)}><div className="history-item-heading"><Undo2 /><strong>Version {String(item.version)}</strong><span>{titleCase(String(item.change_type))}</span></div><p>{String(item.reason || "Configuration revision")}</p><small>{new Date(String(item.created_at)).toLocaleString("en-IN")}</small></div>)}</div></div></div>}
    {modal === "master" && selectedMaster && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Edit {entityLabels[active].replace(/s$/, "")}</h2><p>Version {String(selectedMaster.version)} · changes are audited</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={updateMaster}><div className="form-grid">{masterFields.map((field) => <label className={`field ${field.type === "checkbox" ? "check-field" : ""}`} key={field.name}>{field.type === "checkbox" ? <><input name={field.name} type="checkbox" defaultChecked={Boolean(selectedMaster[field.name])} /><span>{field.label}</span></> : <><span>{field.label}</span>{field.type === "multiselect" ? <select name={field.name} multiple size={4} defaultValue={Array.isArray(selectedMaster[field.name]) ? selectedMaster[field.name] as string[] : []}>{field.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select> : <input name={field.name} type={field.type || "text"} required={field.required} min={field.type === "number" ? (field.name === "duration_terms" ? 1 : 0) : undefined} defaultValue={field.name === "network_cidrs" ? (selectedMaster.network_cidrs as string[] || []).join(", ") : field.type === "datetime-local" ? localDateTime(selectedMaster[field.name]) : String(selectedMaster[field.name] ?? "")} />}</>}</label>)}<label className="field full-field"><span>Reason for change</span><input name="reason" minLength={8} required /></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button className="primary-button" disabled={saving}>Save revision</button></footer></form></div></div>}
    {modal === "master-status" && selectedMaster && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{activateMaster ? "Activate" : "Retire"} record</h2><p>{String(selectedMaster.name || selectedMaster.code || selectedMaster.label)} · dependent records are checked before saving</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={setMasterActive}><div className="form-grid one-column"><label className="field"><span>Reason</span><input name="reason" minLength={8} required /></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>{activateMaster ? "Activate" : "Retire"}</button></footer></form></div></div>}
    {modal === "master-history" && selectedMaster && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Revision history</h2><p>{String(selectedMaster.name || selectedMaster.code || selectedMaster.label)}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><div className="history-list">{historyRows.length ? historyRows.map((item) => <div className="history-item" key={String(item.version)}><strong>Version {String(item.version)}</strong><p>{String(item.reason || item.change_type)}</p><small>{new Date(String(item.created_at)).toLocaleString("en-IN")}</small></div>) : <div className="empty-state">No revisions recorded before this feature was enabled.</div>}</div></div></div>}
    {modal === "calendar-exception" && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Request overlap exception</h2><p>An independent administrator must approve this exact date window.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={requestCalendarException}><div className="form-grid one-column">{active === "terms" && <label className="field"><span>Academic year</span><select name="academic_year_id" required><option value="">Select year</option>{(catalog.academic_years || []).map((row) => <option value={id(row)} key={id(row)}>{String(row.label)}</option>)}</select></label>}<label className="field"><span>Existing record to edit (optional)</span><select name="target_id"><option value="">New record</option>{rows.map((row) => <option value={id(row)} key={id(row)}>{String(row.label || row.name)}</option>)}</select></label><label className="field"><span>Starts on</span><input name="starts_on" type="date" required /></label><label className="field"><span>Ends on</span><input name="ends_on" type="date" required /></label><label className="field"><span>Reason</span><input name="reason" minLength={12} required /></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>Request approval</button></footer></form></div></div>}
  </div>;
}

function PaperFields({ paper }: { paper: Record<string, unknown> }) {
  const rules = paper.rules as { critical_change?: boolean; second_valuation_mark_threshold?: string | null } | undefined;
  return <div className="form-grid"><label className="field full-field"><span>Paper title</span><input name="title" defaultValue={String(paper.title)} required /></label><label className="field"><span>Maximum marks</span><input name="max_marks" type="number" min="0.1" step="0.1" defaultValue={markInputValue(paper.max_marks)} required /></label><label className="field"><span>Passing marks</span><input name="pass_marks" type="number" min="0" step="0.1" defaultValue={markInputValue(paper.pass_marks)} required /></label><label className="field"><span>Valuation rounds</span><input name="valuation_rounds" type="number" min="1" max="3" defaultValue={String(paper.valuation_rounds)} required /></label><label className="field"><span>Round 2 if score exceeds (one-round papers only)</span><input name="second_valuation_mark_threshold" type="number" min="0" step="0.01" defaultValue={rules?.second_valuation_mark_threshold == null ? "" : String(rules.second_valuation_mark_threshold)} /></label><label className="field"><span>Difference threshold between rounds</span><input name="discrepancy_threshold" type="number" min="0" step="0.01" defaultValue={String(paper.discrepancy_threshold)} required /></label><label className="field check-field"><input name="moderation_required" type="checkbox" defaultChecked={Boolean(paper.moderation_required)} /><span>Moderation required</span></label><label className="field check-field"><input name="critical_change" type="checkbox" defaultChecked={Boolean(rules?.critical_change)} /><span>Critical changes need dual approval</span></label></div>;
}

type PaperAction = "submit" | "approve" | "freeze";
type PaperRecordProps = {
  paper: Record<string, unknown>;
  currentUserId: number | null;
  expanded: boolean;
  saving: boolean;
  onToggle: () => void;
  onAddQuestion: () => void;
  onViewQuestions: () => void;
  onEditQuestion: (question: Record<string, unknown>) => void;
  onDeleteQuestion: (question: Record<string, unknown>) => void;
  onEdit: () => void;
  onHistory: () => void;
  onGovernedChange: () => void;
  onAction: (action: PaperAction) => void;
};

function PaperRecord({ paper, currentUserId, expanded, saving, onToggle, onAddQuestion, onViewQuestions, onEditQuestion, onDeleteQuestion, onEdit, onHistory, onGovernedChange, onAction }: PaperRecordProps) {
  const questions = Array.isArray(paper.questions) ? paper.questions as Record<string, unknown>[] : [];
  const readiness = paper.readiness as { ready: boolean; issues?: string[] } | undefined;
  const isDraft = paper.status === "draft";
  const paperId = id(paper);
  const submittedByCurrentUser = currentUserId !== null && String(paper.submitted_by_id) === String(currentUserId);
  const approvedByCurrentUser = currentUserId !== null && String(paper.approved_by_id) === String(currentUserId);
  const cannotFreeze = submittedByCurrentUser || approvedByCurrentUser;

  return <article id={`paper-${paperId}`} className={`paper-record ${expanded ? "expanded" : ""}`}>
    <div className="paper-record-main" role="button" tabIndex={0} aria-expanded={expanded} aria-controls={`paper-questions-${paperId}`} onClick={onToggle} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onToggle(); } }}>
      <header className="paper-record-header">
        <div className="paper-record-title">
          <span className="paper-record-code">{String(paper.code)}</span>
          <h3>{String(paper.title)}</h3>
          <span>{String(paper.subject || "")} · {String(paper.session || "")}</span>
          <span>Version {String(paper.version)}</span>
        </div>
        <div className="paper-record-status">
          <span className={`status-pill ${String(paper.status)}`}>{titleCase(String(paper.status))}</span>
          <span className={`status-pill ${readiness?.ready ? "ready" : "attention"}`}>{readiness?.ready ? "Ready" : "Attention"}</span>
        </div>
      </header>

      <div className="paper-record-metrics" aria-label={`${String(paper.code)} paper details`}>
        <div><span>Maximum marks</span><strong>{String(paper.max_marks)}</strong></div>
        <div><span>Passing marks</span><strong>{String(paper.pass_marks)}</strong></div>
        <div><span>Valuation rounds</span><strong>{String(paper.valuation_rounds)}{(paper.rules as Record<string, unknown> | undefined)?.second_valuation_mark_threshold != null ? `, second above ${String((paper.rules as Record<string, unknown>).second_valuation_mark_threshold)}` : ""}</strong></div>
        <div><span>Difference threshold</span><strong>{String(paper.discrepancy_threshold)}</strong></div>
      </div>
    </div>

    <footer className="paper-record-footer">
      <button className="question-toggle" onClick={onToggle} aria-expanded={expanded} aria-controls={`paper-questions-${paperId}`}>
        <span>{questions.length} {questions.length === 1 ? "question" : "questions"} configured</span><ChevronDown />
      </button>
      <div className="paper-record-actions">
        {isDraft && <button onClick={onAddQuestion} disabled={saving}><FilePlus2 />Add question</button>}
        <button title="View question numbers and assigned marks" onClick={onViewQuestions} disabled={saving}><ClipboardList />Questions &amp; marks</button>
        {isDraft && <button onClick={onEdit} disabled={saving}><Pencil />Edit paper</button>}
        <button onClick={onHistory} disabled={saving}><History />History</button>
        {!isDraft && <button onClick={onGovernedChange} disabled={saving}><ShieldAlert />Request change</button>}
        {isDraft && <button className="paper-primary-action" onClick={() => onAction("submit")} disabled={saving}>Submit</button>}
        {paper.status === "review" && <><button className="paper-primary-action" onClick={() => onAction("approve")} disabled={saving || submittedByCurrentUser} title={submittedByCurrentUser ? "A different administrator must approve this paper" : "Approve this paper"}>Approve</button>{submittedByCurrentUser && <small className="action-restriction">A different administrator must approve this paper.</small>}</>}
        {paper.status === "approved" && <><button className="paper-primary-action" onClick={() => onAction("freeze")} disabled={saving || cannotFreeze} title={cannotFreeze ? "A third administrator must freeze this paper" : "Freeze this paper"}>Freeze</button>{cannotFreeze && <small className="action-restriction">A third administrator must freeze this paper.</small>}</>}
        {paper.status === "frozen" && <span className="locked-label"><Check />Frozen</span>}
      </div>
    </footer>

    {expanded && <section className="paper-question-list" id={`paper-questions-${paperId}`} aria-label={`${String(paper.code)} questions`}>
      <div className="paper-question-list-heading"><strong>Question breakdown</strong><span>Question number and maximum marks</span></div>
      {questions.length ? questions.map((question) => <div className="paper-question-row" key={id(question)}>
        <strong>Question {String(question.number)}{question.sub_question ? ` (${String(question.sub_question)})` : ""}</strong>
        <span>{question.required ? "Required" : "Optional"}</span>
        <b>{String(question.max_marks)} marks</b>
        {isDraft && <div className="row-actions"><button title="Edit question" aria-label={`Edit question ${String(question.number)}`} onClick={() => onEditQuestion(question)} disabled={saving}><Pencil /></button><button title="Remove question" aria-label={`Remove question ${String(question.number)}`} onClick={() => onDeleteQuestion(question)} disabled={saving}><X /></button></div>}
      </div>) : <p className="paper-question-empty">No questions have been added to this paper yet. Use “Add question” to create the first one.</p>}
      {isDraft && <button className="secondary-button" onClick={onAddQuestion} disabled={saving}><Plus />Add question</button>}
      {!readiness?.ready && readiness?.issues?.length ? <p className="paper-readiness-note">{readiness.issues.join(" · ")}</p> : null}
    </section>}
  </article>;
}

function QuestionMarksRecord({ paper, subject, saving, editable, onAddQuestion, onEditQuestion, onDeleteQuestion }: { paper: Record<string, unknown>; subject: string; saving: boolean; editable: boolean; onAddQuestion: () => void; onEditQuestion: (question: Record<string, unknown>) => void; onDeleteQuestion: (question: Record<string, unknown>) => void }) {
  const questions = (Array.isArray(paper.questions) ? paper.questions as Record<string, unknown>[] : []).slice().sort((left, right) => Number(left.position) - Number(right.position));

  return <section className="question-marks-record">
    <div className="question-marks-paper"><div><span>Paper name</span><strong>{String(paper.title)}</strong></div><div><span>Subject</span><strong>{subject}</strong></div></div>
    <div className="question-marks-heading"><span>Question number</span><span>Assigned marks</span><span>Required</span><span>Actions</span></div>
    {questions.length ? questions.map((question) => <div className="question-marks-row" key={id(question)}><strong>Q. {String(question.number)}{question.sub_question ? ` (${String(question.sub_question)})` : ""}</strong><b>{String(question.max_marks)} marks</b><span>{question.required ? "Required" : "Optional"}</span>{editable ? <div className="row-actions"><button title="Edit question" aria-label={`Edit question ${String(question.number)}`} onClick={() => onEditQuestion(question)} disabled={saving}><Pencil /></button><button title="Remove question" aria-label={`Remove question ${String(question.number)}`} onClick={() => onDeleteQuestion(question)} disabled={saving}><X /></button></div> : <span />}</div>) : <p className="question-marks-empty">No questions have been assigned to this paper.</p>}
    {editable && <footer className="modal-footer"><button className="primary-button" onClick={onAddQuestion} disabled={saving}><Plus />Add question &amp; marks</button></footer>}
    <AIReferenceSetup paperId={id(paper)} />
  </section>;
}
