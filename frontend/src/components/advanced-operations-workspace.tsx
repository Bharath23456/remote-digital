"use client";
/* eslint-disable react-hooks/set-state-in-effect */

import {
  Activity, AlertTriangle, Banknote, Bell, Building2, CheckCircle2, ClipboardCheck,
  ChevronLeft, ChevronRight, CloudCog, FileCheck2, FileSearch, Fingerprint, Gauge, Languages, Link2,
  LoaderCircle, MonitorUp, Play, Plus, RefreshCw, RotateCcw, Send, ShieldCheck, X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { csrfFetch } from "@/lib/api";
import { DynamicFields, readDynamicFields, useDynamicFields } from "@/components/dynamic-fields";
import { ProctoringReviewPanel } from "@/components/proctoring-review-panel";

type Row = Record<string, unknown> & { id: string; status?: string; version?: number };
type Reference = { id: string; [key: string]: unknown };
type RemoteSupportSession = Row & {
  assignment_id: string;
  script: string;
  paper: string;
  evaluator: string;
  requested_by: string;
  reason: string;
  expires_at: string;
  commands?: { id: string; kind: string; status: string; result: string }[];
};
type Catalog = Record<string, unknown> & {
  references?: { papers: Reference[]; evaluators: Reference[]; sessions: Reference[]; scripts: Reference[]; final_marks: Reference[]; valuation_results: Reference[]; users: Reference[] };
  monitoring?: Record<string, unknown>;
  productivity?: Record<string, unknown>;
  active_evaluations?: Row[];
  remote_support_sessions?: RemoteSupportSession[];
};
export type AdvancedSection = "assessment" | "operations" | "services" | "platform" | "centres";

const sections: Record<AdvancedSection, string[]> = {
  assessment: ["moderation", "revaluation", "completion"],
  operations: ["security", "monitoring", "workload", "issues", "runtime", "notifications", "continuity"],
  services: ["remuneration", "student"],
  platform: ["audit", "integrations", "languages", "recovery"],
  centres: ["centres"],
};
const advancedTabStorageKey = (section: AdvancedSection) => `admiezo-advanced-tab-${section}`;

const modules: Record<string, { number: string; title: string; detail: string; data: string; icon: typeof Activity; columns: string[] }> = {
  moderation: { number: "24", title: "Moderation", detail: "Deterministic sampling, independent review and approved adjustments.", data: "moderation_policies", icon: ClipboardCheck, columns: ["paper_id", "sample_percentage", "sampling_modes", "mandatory", "version"] },
  revaluation: { number: "27", title: "Revaluation & recounting", detail: "Full revaluation or targeted missed-mark recounting with independent review and recorded outcomes.", data: "revaluations", icon: RotateCcw, columns: ["request_type", "script_id", "recounting_notes", "original_mark_snapshot", "new_mark", "status"] },
  completion: { number: "28", title: "Completion control", detail: "Completeness checks, examiner declaration, signatures and controlled release.", data: "completions", icon: FileCheck2, columns: ["script_id", "checks", "signature_digest", "status"] },
  security: { number: "29", title: "Remote security", detail: "Restricted viewer controls, session fingerprints and risk responses.", data: "presence_events", icon: Fingerprint, columns: ["assignment_id", "category", "severity", "action", "created_at"] },
  monitoring: { number: "31", title: "Live monitoring", detail: "Attendance, live throughput, remaining work and deadline risk.", data: "attendance", icon: Activity, columns: ["evaluator_id", "role", "checked_in_at", "checked_out_at", "active_seconds", "exception"] },
  workload: { number: "32", title: "Productivity & workload", detail: "Capacity signals and independently approved rebalancing actions.", data: "workload_actions", icon: Gauge, columns: ["evaluator_id", "action", "reason", "metrics", "status"] },
  issues: { number: "33", title: "Issue management", detail: "Classification, duplicate detection, SLA routing and locked resolutions.", data: "issues", icon: AlertTriangle, columns: ["title", "classification", "priority", "owner_id", "sla_due_at", "status"] },
  runtime: { number: "34", title: "Runtime recovery", detail: "Detected incidents, retries, degraded operation and recovery evidence.", data: "runtime_incidents", icon: CloudCog, columns: ["service", "category", "severity", "retry_count", "recovered_at", "status"] },
  notifications: { number: "35", title: "Notifications", detail: "Multi-channel delivery, retries, acknowledgements and escalation.", data: "notifications", icon: Bell, columns: ["title", "body", "category", "severity", "created_at", "status"] },
  centres: { number: "36", title: "Centres & camps", detail: "Capacity, readiness, secure operations and camp lifecycle.", data: "centres", icon: Building2, columns: ["code", "name", "location", "capacity", "workstation_count", "status"] },
  continuity: { number: "37", title: "Low-bandwidth continuity", detail: "Online state, recoverable browser queue and conservative retry handling.", data: "runtime_incidents", icon: RefreshCw, columns: ["service", "category", "status", "recovered_at"] },
  remuneration: { number: "38", title: "Remuneration", detail: "Rule-based calculation, independent approval, payment and reconciliation.", data: "statements", icon: Banknote, columns: ["evaluator_id", "units", "gross_amount", "deductions", "net_amount", "status"] },
  student: { number: "39", title: "Photocopy requests", detail: "Approve, expire, revoke, reissue and track university delivery.", data: "student_requests", icon: FileSearch, columns: ["source_system", "external_application_id", "script_id", "release_mode", "status", "expires_at", "delivered_at", "delivery_reference"] },
  audit: { number: "42", title: "Audit & forensics", detail: "Reconstructed script timelines and SHA-256 sealed evidence packages.", data: "evidence_packages", icon: ShieldCheck, columns: ["script_id", "purpose", "event_count", "digest", "status"] },
  integrations: { number: "46", title: "Integrations", detail: "Secret references, idempotent result handover and acknowledgement reconciliation.", data: "handovers", icon: Link2, columns: ["endpoint_id", "final_mark_id", "payload_digest", "attempt_count", "differences", "status"] },
  languages: { number: "48", title: "Language preferences", detail: "Ten interface locales with persisted per-user selection and RTL support.", data: "integrations", icon: Languages, columns: ["name", "kind", "status"] },
  recovery: { number: "49", title: "Disaster recovery", detail: "Replication objectives, controlled drills, integrity checks and activation gates.", data: "recovery_drills", icon: CloudCog, columns: ["plan_id", "drill_type", "measurements", "integrity_checks", "report", "status"] },
};

const label = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const display = (value: unknown) => {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}T/.test(text)) return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(text));
  return text.length > 38 ? `${text.slice(0, 35)}…` : label(text);
};

function SelectField({ title, value, onChange, options, optionLabel }: { title: string; value: string; onChange: (value: string) => void; options: Reference[]; optionLabel: (item: Reference) => string }) {
  return <label className="field"><span>{title}</span><select value={value} onChange={(event) => onChange(event.target.value)} required><option value="">Select</option>{options.map((item) => <option key={item.id} value={item.id}>{optionLabel(item)}</option>)}</select></label>;
}

export function AdvancedOperationsWorkspace({ section, initialTab, visibleTabs }: { section: AdvancedSection; initialTab?: string; visibleTabs?: string[] }) {
  const tabs = visibleTabs || sections[section];
  const notificationFields = useDynamicFields("notification");
  const [tab, setTab] = useState(tabs[0]);
  const [catalog, setCatalog] = useState<Catalog>({});
  const [i18n, setI18n] = useState<{ locales: { code: string; native: string; name: string; direction: string }[]; selected: string }>({ locales: [], selected: "en" });
  const [form, setForm] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [online, setOnline] = useState(true);
  const [queued, setQueued] = useState(0);
  const [supportTarget, setSupportTarget] = useState<Row | null>(null);
  const [supportReason, setSupportReason] = useState("");
  const [supportSessionId, setSupportSessionId] = useState("");

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [catalogResponse, localeResponse] = await Promise.all([csrfFetch(`/api/v1/phase4/catalog?section=${section}`), csrfFetch("/api/v1/phase4/i18n")]);
      if (!catalogResponse.ok) throw new Error("Could not load the operational workspace");
      setCatalog(await catalogResponse.json());
      if (localeResponse.ok) setI18n(await localeResponse.json());
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load the workspace"); }
    finally { setLoading(false); }
  }, [section]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const update = () => { setOnline(navigator.onLine); setQueued(JSON.parse(localStorage.getItem("admiezo-continuity-queue") || "[]").length); };
    update(); window.addEventListener("online", update); window.addEventListener("offline", update);
    return () => { window.removeEventListener("online", update); window.removeEventListener("offline", update); };
  }, []);
  useEffect(() => {
    const saved = localStorage.getItem(advancedTabStorageKey(section));
    setTab(initialTab && tabs.includes(initialTab) ? initialTab : saved && tabs.includes(saved) ? saved : tabs[0]);
  }, [initialTab, section, tabs]);
  useEffect(() => {
    if (section !== "operations" || tab !== "monitoring") return;
    const interval = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(interval);
  }, [load, section, tab]);

  function selectTab(key: string) {
    setTab(key); setForm({}); setMessage(""); setError("");
    localStorage.setItem(advancedTabStorageKey(section), key);
  }

  const refs = catalog.references || { papers: [], evaluators: [], sessions: [], scripts: [], final_marks: [], valuation_results: [], users: [] };
  const meta = modules[tab];
  const rows = useMemo(() => (Array.isArray(catalog[meta.data]) ? catalog[meta.data] as Row[] : []), [catalog, meta.data]);
  const monitoring = catalog.monitoring as Record<string, unknown> | undefined;
  const productivity = catalog.productivity as Record<string, unknown> | undefined;

  async function request(path: string, method: "POST" | "PUT" = "POST", body: Record<string, unknown> = {}) {
    setBusy(true); setError(""); setMessage("");
    try {
      const response = await csrfFetch(`/api/v1/phase4${path}`, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "The operation could not be completed");
      setMessage("Operation completed and added to the audit trail."); setForm({}); await load();
      return result;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The operation could not be completed"); return null; }
    finally { setBusy(false); }
  }

  async function submitSupportRequest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!supportTarget) return;
    const result = await request("/remote-support/requests", "POST", {
      assignment_id: supportTarget.id,
      reason: supportReason,
    });
    if (result) {
      setSupportSessionId(String(result.id || ""));
      setSupportTarget(null);
      setSupportReason("");
    }
  }

  async function sendSupportCommand(session: RemoteSupportSession, kind: "previous_page" | "next_page" | "refresh_viewer") {
    await request(`/remote-support/${session.id}/commands`, "POST", { kind });
  }

  async function endSupport(session: RemoteSupportSession) {
    const result = await request(`/remote-support/${session.id}/end`);
    if (result) setSupportSessionId("");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const customFields = readDynamicFields(new FormData(event.currentTarget), notificationFields);
    if (tab === "moderation") { const policy = (catalog.moderation_policies as Row[] || []).find((item) => item.paper_id === form.paper_id); await request("/moderation/policies", "PUT", { paper_id: form.paper_id, version: policy?.version, sample_percentage: Number(form.sample_percentage || 10), sampling_modes: ["percentage_random", "failed_script", "high_score"], high_score_threshold: 85, mandatory: form.mandatory === "true" }); }
    if (tab === "revaluation") await request("/revaluation/requests", "POST", { script_id: form.script_id, identity_reference: form.identity_reference, scope: form.request_type === "recounting" ? "questions" : "full", question_ids: String(form.question_ids || "").split(",").map((item) => item.trim()).filter(Boolean), reason: form.reason, rule: form.rule || "best", request_type: form.request_type || "revaluation", recounting_notes: form.recounting_notes || "" });
    if (tab === "completion") await request(`/completion/final-marks/${form.final_mark_id}/review`);
    if (tab === "monitoring") await request("/monitoring/attendance", "POST", { evaluator_id: form.evaluator_id, session_id: form.session_id, role: "evaluator", action: form.action || "check_in", active_seconds: Number(form.active_seconds || 0), idle_seconds: Number(form.idle_seconds || 0) });
    if (tab === "workload") await request("/workload/actions", "POST", { evaluator_id: form.evaluator_id, action: form.action || "rebalance", reason: form.reason, metrics: productivity || {} });
    if (tab === "issues") await request("/issues", "POST", { issue_type: form.issue_type || "technical", title: form.title, description: form.description });
    if (tab === "runtime") await request("/runtime/incidents", "POST", { service: form.service, category: form.category, severity: form.severity || "medium", last_confirmed_state: {}, recovery_point: {}, details: { note: form.details } });
    if (tab === "notifications") await request("/notifications", "POST", { user_id: Number(form.user_id), category: form.category || "operations", title: form.title, body: form.description, severity: form.severity || "normal", channels: ["in_app"], mandatory_acknowledgement: form.mandatory === "true", custom_fields: customFields });
    if (tab === "centres" && form.operation !== "camp") await request("/centres", "POST", { code: form.code, name: form.title, location: form.location, capacity: Number(form.capacity), workstation_count: Number(form.workstations), schedule: {}, security_controls: ["restricted-entry", "secure-lan"] });
    if (tab === "centres" && form.operation === "camp") { const session = refs.sessions.find((item) => item.id === form.session_id); await request("/centres/camps", "POST", { centre_id: form.centre_id, session_id: form.session_id, name: form.title, starts_at: session?.evaluation_starts_at, ends_at: session?.evaluation_ends_at, evaluator_ids: [] }); }
    if (tab === "remuneration") await request("/remuneration/calculate", "POST", { evaluator_id: form.evaluator_id, session_id: form.session_id, rule_id: form.rule_id });
    if (tab === "student") await request("/student/requests", "POST", { identity_reference: form.identity_reference, script_id: form.script_id, purpose: form.purpose || "copy", release_mode: form.release_mode || "masked" });
    if (tab === "audit") await request("/audit/evidence", "POST", { script_id: form.script_id, purpose: form.purpose || "audit" });
    if (tab === "integrations" && form.operation !== "endpoint") await request("/integrations/handovers", "POST", { endpoint_id: form.endpoint_id, final_mark_id: form.final_mark_id, idempotency_key: form.idempotency_key });
    if (tab === "integrations" && form.operation === "endpoint") await request("/integrations", "POST", { name: form.title, kind: form.category, base_url: form.base_url, authentication: "oauth2_client", secret_reference: form.secret_reference, rate_limit_per_minute: 60, webhook_events: ["result.acknowledged", "result.rejected"] });
    if (tab === "languages") await request("/i18n", "PUT", { locale: form.locale || i18n.selected, additional_locales: [] });
    if (tab === "recovery" && form.operation !== "plan") await request("/continuity/drills", "POST", { plan_id: form.plan_id, drill_type: form.drill_type || "database-and-object-restore", recovery_point: { type: "latest_verified" } });
    if (tab === "recovery" && form.operation === "plan") await request("/continuity/plans", "POST", { name: form.title, regions: form.regions.split(",").map((item) => item.trim()).filter(Boolean), database_replication: { mode: "point-in-time" }, file_replication: { mode: "immutable-copy" }, rpo_minutes: Number(form.rpo || 15), rto_minutes: Number(form.rto || 60), clean_environment: form.clean_environment });
  }

  async function rowAction(row: Row, target: string) {
    const version = Number(row.version || 1);
    if (tab === "moderation") return request(`/moderation/policies/${row.id}/sample`);
    if (tab === "revaluation") return request(`/revaluation/requests/${row.id}/action`, "POST", { version, target, reason: form.reason || "Independent revaluation review completed with recorded evidence.", values: { evaluator_id: form.evaluator_id, result_id: form.result_id } });
    if (tab === "completion" && target === "release") return request("/completion/authorizations", "POST", { kind: "result_release", final_mark_id: row.final_mark_id, purpose: "Release the signed and verified final result to approved downstream services.", proposed_change: {} });
    if (tab === "completion") return request(`/completion/records/${row.id}/sign`, "POST", { version, declaration: "I confirm the evaluation is complete and the final mark evidence is accurate.", confirm: true });
    if (tab === "workload") return request(`/workload/actions/${row.id}/decision`, "POST", { version, target });
    if (tab === "issues") return request(`/issues/${row.id}/action`, "POST", { version, target, reason: target === "resolved" ? "Operational evidence confirms the reported issue is resolved." : "", values: {} });
    if (tab === "runtime") return request(`/runtime/incidents/${row.id}/action`, "POST", { version, target, values: {} });
    if (tab === "notifications") return request(`/notifications/${row.id}/action`, "POST", { version, target, values: {} });
    if (tab === "centres") {
      if (target === "readiness") return request(`/centres/${row.id}/readiness`, "POST", { scanner_ready: true, workstation_ready: true, network_ready: true, power_ready: true, secure_lan_ready: true, operators_ready: true, seat_plan: {}, notes: "Opening checklist verified." });
      return request(`/centres/${row.id}/action`, "POST", { version, target, values: {} });
    }
    if (tab === "remuneration") return request(`/remuneration/statements/${row.id}/action`, "POST", { version, target, values: target === "paid" ? { payment_reference: `PAY-${row.id.slice(0, 12).toUpperCase()}` } : {} });
    if (tab === "student") {
      if (target === "viewer") { const response = await csrfFetch(`/api/v1/phase4/student/requests/${row.id}/viewer`); const result = await response.json().catch(() => ({})); if (!response.ok) setError(result.detail || "Viewer unavailable"); else setMessage(`${result.pages?.length || 0} watermarked page(s) are available for five minutes.`); return result; }
      if (["revoke", "reissue"].includes(target)) return request(`/student/requests/${row.id}/action`, "POST", { version, target, reason: target === "revoke" ? "Photocopy release revoked by administrator." : "Replacement photocopy requested after expiry or revocation." });
      return request(`/student/requests/${row.id}/decision`, "POST", { version, approve: target === "approve", release_mode: form.release_mode || "masked" });
    }
    if (tab === "audit") return request(`/audit/evidence/${row.id}/seal`);
    if (tab === "integrations") return request(`/integrations/handovers/${row.id}/action`, "POST", { version, target, values: {} });
    if (tab === "recovery") return request(`/continuity/drills/${row.id}/action`, "POST", { version, target, reason: target === "passed" ? "Recovery objectives and integrity checks passed." : "", values: target === "verifying" ? { measurements: { rto_minutes: 42 }, integrity_checks: { database: true, objects: true } } : {} });
  }

  async function auxiliaryAction(kind: string, row: Row, target: string) {
    const version = Number(row.version || 1);
    if (kind === "Moderation cases") return request(`/moderation/cases/${row.id}/action`, "POST", { version, target, reason: form.reason || "Independent moderation evidence supports this recorded decision.", values: { moderator_id: form.evaluator_id, adjusted_mark: Number(form.adjusted_mark || row.original_mark || 0), mark_snapshot: { source: "fresh_moderation" } } });
    if (kind === "Release authorizations") {
      if (target === "consume") return request(`/completion/authorizations/${row.id}/consume`, "POST", { version, target, values: {} });
      return request(`/completion/authorizations/${row.id}/decision`, "POST", { version, approve: target === "approve" });
    }
    if (kind === "Evaluation camps") return request(`/centres/camps/${row.id}/action`, "POST", { version, target, values: target === "closed" ? { performance: { closure_verified: true }, incidents: [] } : {} });
    if (kind === "Recovery plans") return request(`/continuity/plans/${row.id}/action`, "POST", { version, target, values: {} });
  }

  const auxiliaryNext = (kind: string, row: Row) => {
    const status = String(row.status || "");
    if (kind === "Moderation cases") return status === "sampled" ? ["assigned", "Assign"] : status === "assigned" ? ["review", "Start review"] : status === "review" ? ["decided", "Record decision"] : status === "decided" ? ["approved", "Approve"] : null;
    if (kind === "Release authorizations") return status === "requested" ? ["approve", "Approve"] : status === "approved" ? ["consume", "Release"] : null;
    if (kind === "Evaluation camps") return status === "planned" ? ["active", "Start camp"] : status === "active" ? ["closed", "Close camp"] : null;
    if (kind === "Recovery plans") return status === "draft" ? ["approved", "Approve"] : status === "approved" ? ["active", "Activate"] : null;
    return null;
  };

  const nextAction = (row: Row): { target: string; text: string } | null => {
    const status = String(row.status || "");
    if (tab === "workload") return status === "proposed" ? { target: "approved", text: "Approve" } : status === "approved" ? { target: "executed", text: "Execute" } : null;
    if (tab === "revaluation") return status === "requested" ? { target: "approved", text: "Approve" } : status === "approved" ? { target: "assigned", text: "Assign" } : status === "assigned" ? { target: "evaluated", text: "Attach result" } : status === "evaluated" ? { target: "decided", text: "Decide" } : status === "decided" ? { target: "closed", text: "Close" } : null;
    if (tab === "completion") return status === "ready" ? { target: "sign", text: "Declare & sign" } : status === "signed" ? { target: "release", text: "Request release" } : null;
    if (tab === "issues") return status === "open" ? { target: "assigned", text: "Assign" } : ["assigned", "escalated", "reopened"].includes(status) ? { target: "resolved", text: "Resolve" } : status === "resolved" ? { target: "confirmed", text: "Confirm" } : null;
    if (tab === "runtime") return ["detected", "degraded", "failed"].includes(status) ? { target: "retrying", text: "Retry" } : status === "retrying" ? { target: "recovered", text: "Recover" } : null;
    if (tab === "notifications") return status === "queued" ? { target: "deliver", text: "Deliver" } : status === "failed" ? { target: "retry", text: "Retry" } : status === "delivered" ? { target: "acknowledge", text: "Acknowledge" } : null;
    if (tab === "centres") return status === "draft" ? { target: "readiness", text: "Check readiness" } : status === "review" ? { target: "ready", text: "Mark ready" } : status === "ready" ? { target: "active", text: "Activate" } : status === "active" ? { target: "closed", text: "Close" } : null;
    if (tab === "remuneration") return status === "calculated" ? { target: "approved", text: "Approve" } : status === "approved" ? { target: "paid", text: "Mark paid" } : status === "paid" ? { target: "reconciled", text: "Reconcile" } : null;
    if (tab === "student") return status === "requested" ? { target: "approve", text: "Approve" } : ["approved", "available"].includes(status) ? { target: "viewer", text: "Open viewer" } : ["expired", "revoked"].includes(status) ? { target: "reissue", text: "Reissue" } : null;
    if (tab === "audit") return status === "requested" ? { target: "seal", text: "Seal package" } : null;
    if (tab === "integrations") return status === "queued" ? { target: "sent", text: "Mark sent" } : status === "failed" ? { target: "queued", text: "Retry" } : status === "acknowledged" ? { target: "reconciled", text: "Reconcile" } : status === "reconciled" ? { target: "confirmed", text: "Confirm" } : null;
    if (tab === "recovery") return status === "planned" ? { target: "running", text: "Start drill" } : status === "running" ? { target: "verifying", text: "Verify" } : status === "verifying" ? { target: "passed", text: "Pass drill" } : null;
    return null;
  };

  const formFields = () => {
    if (["security", "continuity"].includes(tab)) return null;
    if (tab === "moderation") { const policy = (catalog.moderation_policies as Row[] || []).find((item) => item.paper_id === form.paper_id); return <><SelectField title="Paper" value={form.paper_id || ""} onChange={(value) => setForm({ ...form, paper_id: value })} options={refs.papers} optionLabel={(item) => `${item.code} · ${item.title}`} /><label className="field"><span>Sample percentage</span><input type="number" min="0" max="100" value={form.sample_percentage || "10"} onChange={(event) => setForm({ ...form, sample_percentage: event.target.value })} /></label><SelectField title="Moderator for case actions" value={form.evaluator_id || ""} onChange={(value) => setForm({ ...form, evaluator_id: value })} options={refs.evaluators.filter((item) => item.status === "active")} optionLabel={(item) => `${item.evaluator_code} · ${item.display_name}`} /><label className="field"><span>Adjusted mark</span><input type="number" min="0" value={form.adjusted_mark || ""} onChange={(event) => setForm({ ...form, adjusted_mark: event.target.value })} /></label><label className="field"><span>Decision reason</span><input value={form.reason || ""} onChange={(event) => setForm({ ...form, reason: event.target.value })} /></label><input type="hidden" value={String(policy?.version || "")} readOnly /></>;
    }
    if (tab === "revaluation") return <><SelectField title="Finalized script" value={form.script_id || ""} onChange={(value) => setForm({ ...form, script_id: value })} options={refs.scripts.filter((item) => item.state === "finalized")} optionLabel={(item) => String(item.script_code)} /><label className="field"><span>Opaque student reference</span><input value={form.identity_reference || ""} onChange={(event) => setForm({ ...form, identity_reference: event.target.value })} required /></label><label className="field"><span>Reason / decision evidence</span><input value={form.reason || ""} onChange={(event) => setForm({ ...form, reason: event.target.value })} required /></label><SelectField title="Independent evaluator" value={form.evaluator_id || ""} onChange={(value) => setForm({ ...form, evaluator_id: value })} options={refs.evaluators.filter((item) => item.status === "active")} optionLabel={(item) => `${item.evaluator_code} · ${item.display_name}`} /><SelectField title="Locked revaluation result" value={form.result_id || ""} onChange={(value) => setForm({ ...form, result_id: value })} options={refs.valuation_results} optionLabel={(item) => `${String(item.script_id).slice(0, 8)} · ${item.total_marks} marks`} /></>;
    if (tab === "completion") return <SelectField title="Locked final mark" value={form.final_mark_id || ""} onChange={(value) => setForm({ ...form, final_mark_id: value })} options={refs.final_marks.filter((item) => item.status === "locked")} optionLabel={(item) => `${String(item.script_id).slice(0, 8)} · ${item.mark} marks`} />;
    if (["monitoring", "workload", "remuneration"].includes(tab)) return <><SelectField title="Evaluator" value={form.evaluator_id || ""} onChange={(value) => setForm({ ...form, evaluator_id: value })} options={refs.evaluators.filter((item) => item.status === "active")} optionLabel={(item) => `${item.evaluator_code} · ${item.display_name}`} />{tab !== "workload" && <SelectField title="Exam session" value={form.session_id || ""} onChange={(value) => setForm({ ...form, session_id: value })} options={refs.sessions} optionLabel={(item) => String(item.name)} />}{tab === "monitoring" && <label className="field"><span>Attendance action</span><select value={form.action || "check_in"} onChange={(event) => setForm({ ...form, action: event.target.value })}><option value="check_in">Check in</option><option value="check_out">Check out</option></select></label>}{tab === "workload" && <label className="field"><span>Reason</span><input value={form.reason || ""} onChange={(event) => setForm({ ...form, reason: event.target.value })} required /></label>}{tab === "remuneration" && <SelectField title="Payment rule" value={form.rule_id || ""} onChange={(value) => setForm({ ...form, rule_id: value })} options={(catalog.remuneration_rules as Reference[]) || []} optionLabel={(item) => `₹${item.per_script}/script · ${item.tax_percentage}% tax`} />}</>;
    if (["issues", "runtime", "notifications", "centres"].includes(tab)) return <>{tab === "centres" && <label className="field"><span>Operation</span><select value={form.operation || "centre"} onChange={(event) => setForm({ ...form, operation: event.target.value })}><option value="centre">Create centre</option><option value="camp">Plan camp</option></select></label>}{tab === "centres" && form.operation === "camp" ? <><label className="field"><span>Camp name</span><input value={form.title || ""} onChange={(event) => setForm({ ...form, title: event.target.value })} required /></label><SelectField title="Active centre" value={form.centre_id || ""} onChange={(value) => setForm({ ...form, centre_id: value })} options={(catalog.centres as Reference[] || []).filter((item) => item.status === "active")} optionLabel={(item) => `${item.code} · ${item.name}`} /><SelectField title="Exam session" value={form.session_id || ""} onChange={(value) => setForm({ ...form, session_id: value })} options={refs.sessions} optionLabel={(item) => String(item.name)} /></> : <><label className="field"><span>{tab === "centres" ? "Centre name" : "Title / service"}</span><input value={tab === "runtime" ? form.service || "" : form.title || ""} onChange={(event) => setForm({ ...form, [tab === "runtime" ? "service" : "title"]: event.target.value })} required /></label><label className="field"><span>{tab === "centres" ? "Code" : "Category"}</span><input value={tab === "centres" ? form.code || "" : form.category || ""} onChange={(event) => setForm({ ...form, [tab === "centres" ? "code" : "category"]: event.target.value })} required /></label><label className="field full-field"><span>{tab === "centres" ? "Location" : "Description"}</span><input value={tab === "centres" ? form.location || "" : form.description || ""} onChange={(event) => setForm({ ...form, [tab === "centres" ? "location" : "description"]: event.target.value })} required /></label>{tab === "notifications" && <SelectField title="Recipient" value={form.user_id || ""} onChange={(value) => setForm({ ...form, user_id: value })} options={refs.users} optionLabel={(item) => `${item.name} · ${item.email}`} />}{tab === "centres" && <><label className="field"><span>Capacity</span><input type="number" min="1" value={form.capacity || ""} onChange={(event) => setForm({ ...form, capacity: event.target.value })} required /></label><label className="field"><span>Workstations</span><input type="number" min="0" value={form.workstations || ""} onChange={(event) => setForm({ ...form, workstations: event.target.value })} required /></label></>}</>}</>;
    if (["student", "audit"].includes(tab)) return <><SelectField title="Script" value={form.script_id || ""} onChange={(value) => setForm({ ...form, script_id: value })} options={tab === "student" ? refs.scripts.filter((item) => item.state === "finalized") : refs.scripts} optionLabel={(item) => String(item.script_code)} />{tab === "student" && <label className="field"><span>Opaque student reference</span><input value={form.identity_reference || ""} onChange={(event) => setForm({ ...form, identity_reference: event.target.value })} required /></label>}</>;
    if (tab === "integrations") return <><label className="field"><span>Operation</span><select value={form.operation || "handover"} onChange={(event) => setForm({ ...form, operation: event.target.value })}><option value="handover">Queue result handover</option><option value="endpoint">Register endpoint</option></select></label>{form.operation === "endpoint" ? <><label className="field"><span>Endpoint name</span><input value={form.title || ""} onChange={(event) => setForm({ ...form, title: event.target.value })} required /></label><label className="field"><span>Kind</span><input value={form.category || ""} onChange={(event) => setForm({ ...form, category: event.target.value })} required /></label><label className="field"><span>HTTPS base URL</span><input type="url" value={form.base_url || ""} onChange={(event) => setForm({ ...form, base_url: event.target.value })} required /></label><label className="field"><span>Vault secret reference</span><input value={form.secret_reference || ""} onChange={(event) => setForm({ ...form, secret_reference: event.target.value })} required /></label></> : <><SelectField title="Integration" value={form.endpoint_id || ""} onChange={(value) => setForm({ ...form, endpoint_id: value })} options={(catalog.integrations as Reference[]) || []} optionLabel={(item) => `${item.name} · ${item.kind}`} /><SelectField title="Locked final mark" value={form.final_mark_id || ""} onChange={(value) => setForm({ ...form, final_mark_id: value })} options={refs.final_marks.filter((item) => item.status === "locked")} optionLabel={(item) => `${String(item.script_id).slice(0, 8)} · ${item.mark} marks`} /><label className="field full-field"><span>Idempotency key</span><input value={form.idempotency_key || ""} onChange={(event) => setForm({ ...form, idempotency_key: event.target.value })} required /></label></>}</>;
    if (tab === "languages") return <label className="field"><span>Interface language</span><select value={form.locale || i18n.selected} onChange={(event) => setForm({ ...form, locale: event.target.value })}>{i18n.locales.map((item) => <option key={item.code} value={item.code}>{item.native} · {item.name}</option>)}</select></label>;
    if (tab === "recovery") return <><label className="field"><span>Operation</span><select value={form.operation || "drill"} onChange={(event) => setForm({ ...form, operation: event.target.value })}><option value="drill">Plan recovery drill</option><option value="plan">Create recovery plan</option></select></label>{form.operation === "plan" ? <><label className="field"><span>Plan name</span><input value={form.title || ""} onChange={(event) => setForm({ ...form, title: event.target.value })} required /></label><label className="field"><span>Regions, comma separated</span><input value={form.regions || ""} onChange={(event) => setForm({ ...form, regions: event.target.value })} required /></label><label className="field"><span>Clean environment</span><input value={form.clean_environment || ""} onChange={(event) => setForm({ ...form, clean_environment: event.target.value })} required /></label></> : <><SelectField title="Recovery plan" value={form.plan_id || ""} onChange={(value) => setForm({ ...form, plan_id: value })} options={(catalog.recovery_plans as Reference[]) || []} optionLabel={(item) => `${item.name} · RPO ${item.rpo_minutes}m`} /><label className="field"><span>Drill type</span><input value={form.drill_type || "database-and-object-restore"} onChange={(event) => setForm({ ...form, drill_type: event.target.value })} /></label></>}</>;
    return null;
  };

  const auxiliary = tab === "moderation" ? { title: "Moderation cases", rows: (catalog.moderation_cases as Row[]) || [], columns: ["script_id", "moderator_id", "sample_reasons", "original_mark", "adjusted_mark", "status"] }
    : tab === "completion" ? { title: "Release authorizations", rows: (catalog.authorizations as Row[]) || [], columns: ["kind", "final_mark_id", "purpose", "expires_at", "status"] }
    : tab === "centres" ? { title: "Evaluation camps", rows: (catalog.camps as Row[]) || [], columns: ["centre_id", "session_id", "name", "starts_at", "ends_at", "status"] }
    : tab === "integrations" ? { title: "Integration endpoints", rows: (catalog.integrations as Row[]) || [], columns: ["name", "kind", "base_url", "authentication", "status"] }
    : tab === "recovery" ? { title: "Recovery plans", rows: (catalog.recovery_plans as Row[]) || [], columns: ["name", "regions", "rpo_minutes", "rto_minutes", "clean_environment", "status"] }
    : null;
  const selectedSupport = (catalog.remote_support_sessions || []).find((item) => item.id === supportSessionId) || null;
  const supportFor = (assignmentId: string) => (catalog.remote_support_sessions || []).find((item) => item.assignment_id === assignmentId);

  if (loading && !Object.keys(catalog).length) return <div className="advanced-loading"><LoaderCircle /> Loading operational controls</div>;
  return <div className="advanced-workspace">
    <div className="workspace-toolbar"><div className="entity-tabs advanced-tabs" role="tablist">{tabs.map((key) => { const item = modules[key]; return <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""} onClick={() => selectTab(key)}><item.icon />{item.title}</button>; })}</div><button className="icon-button" title="Refresh" onClick={load}><RefreshCw /></button></div>
    <section className="advanced-summary"><div><h2>{meta.title}</h2><p>{meta.detail}</p></div><div className="advanced-kpis"><div><strong>{rows.length}</strong><span>Records</span></div>{tab === "monitoring" && <><div><strong>{String(monitoring?.online_evaluators || 0)}</strong><span>Online</span></div><div><strong>{String(monitoring?.remaining_scripts || 0)}</strong><span>Remaining</span></div></>}{tab === "workload" && <div><strong>{String((productivity?.queues as Record<string, unknown>)?.priority || 0)}</strong><span>Priority queue</span></div>}{tab === "continuity" && <><div><strong>{online ? "Online" : "Offline"}</strong><span>Network</span></div><div><strong>{queued}</strong><span>Queued actions</span></div></>}</div></section>
    {message && <div className="success-banner"><CheckCircle2 />{message}</div>}{error && <div className="form-error" role="alert">{error}</div>}
    {formFields() && <form className="panel advanced-command" onSubmit={submit}><header className="panel-header"><div><h3 className="panel-title">{tab === "student" ? "Manual staff request" : "New operation"}</h3><p className="panel-subtitle">{tab === "student" ? "Use only for an approved internal request. University portal requests appear in the register below." : "Validated by the module policy and written with audit evidence."}</p></div></header><div className="form-grid">{tab === "student" && <label className="field"><span>Release option</span><select value={form.release_mode || "masked"} onChange={(event) => setForm({ ...form, release_mode: event.target.value })}><option value="masked">Masked student copy</option><option value="unmasked_identity">Unmasked identity copy</option></select></label>}{tab === "revaluation" && <label className="field"><span>Request type</span><select value={form.request_type || "revaluation"} onChange={(event) => setForm({ ...form, request_type: event.target.value })}><option value="revaluation">Revaluation</option><option value="recounting">Recounting</option></select></label>}{tab === "revaluation" && form.request_type === "recounting" && <><label className="field full-field"><span>Missed question IDs</span><input value={form.question_ids || ""} onChange={(event) => setForm({ ...form, question_ids: event.target.value })} placeholder="Q1, Q2(a)" required /></label><label className="field full-field"><span>Recounting notes</span><textarea value={form.recounting_notes || ""} onChange={(event) => setForm({ ...form, recounting_notes: event.target.value })} required /></label></>}{formFields()}{tab === "notifications" && <DynamicFields fields={notificationFields} />}</div><footer className="modal-footer"><button className="primary-button" disabled={busy}>{busy ? <LoaderCircle /> : <Plus />}{tab === "student" ? "Create request" : "Run operation"}</button></footer></form>}
    {tab === "security" && <><div className="security-note"><Fingerprint /><div><strong>Security events are generated by the evaluator desk.</strong><span>Fullscreen, webcam continuity, display posture, media-device changes and browser visibility are monitored. Critical detections pause marking and enter human review.</span></div></div><ProctoringReviewPanel sessions={(catalog.secure_sessions as Row[]) || []} reviews={(catalog.proctoring_reviews as Row[]) || []} onReload={load}/></>}
    {tab === "monitoring" && <section className="panel"><header className="panel-header"><div><h3 className="panel-title">Active evaluations</h3><p className="panel-subtitle">Accepted and in-progress scripts ordered by due time</p></div></header><div className="table-wrap"><table><thead><tr><th>Script</th><th>Paper</th><th>Evaluator</th><th>Status</th><th>Progress</th><th>Due</th><th>Assistance</th></tr></thead><tbody>{(catalog.active_evaluations || []).length ? (catalog.active_evaluations || []).map((item) => { const support = supportFor(item.id); const open = support && ["requested", "active"].includes(String(support.status)); return <tr key={item.id}><td><strong>{display(item.script)}</strong></td><td>{display(item.paper)}</td><td>{display(item.evaluator)}</td><td><span className={`status-pill ${item.status}`}>{display(item.status)}</span></td><td>{display(item.progress_percent)}%</td><td>{display(item.due_at)}</td><td><div className="row-actions">{open ? <button onClick={() => setSupportSessionId(support.id)}><MonitorUp />{support.status === "active" ? "Control viewer" : "Awaiting approval"}</button> : <button onClick={() => setSupportTarget(item)}><MonitorUp />Request help</button>}</div></td></tr>; }) : <tr><td colSpan={7}><div className="compact-empty">No evaluations are currently active.</div></td></tr>}</tbody></table></div></section>}
    {tab === "continuity" && <div className="security-note"><RefreshCw /><div><strong>{online ? "Connection available" : "Continuity mode active"}</strong><span>Only non-sensitive navigation progress is queued locally. Marks, script images, identity data and authentication material are never stored in browser persistence.</span></div></div>}
    {tab !== "security" && <section className="panel"><header className="panel-header"><div><h3 className="panel-title">{meta.title} register</h3><p className="panel-subtitle">Tenant-scoped operational records</p></div></header><div className="table-wrap"><table><thead><tr>{meta.columns.map((column) => <th key={column}>{label(column)}</th>)}<th>Action</th></tr></thead><tbody>{rows.length ? rows.map((row) => { const action = nextAction(row); return <tr key={row.id}>{meta.columns.map((column) => <td key={column} title={typeof row[column] === "string" ? String(row[column]) : undefined}>{column === "status" ? <span className={`status-pill ${row[column]}`}>{display(row[column])}</span> : column === "body" ? <span className="notification-body-cell">{String(row[column] || "—")}</span> : display(row[column])}</td>)}<td><div className="row-actions">{tab === "moderation" && <button disabled={busy} onClick={() => rowAction(row, "sample")}><Play />Sample</button>}{action && <button disabled={busy} onClick={() => rowAction(row, action.target)}><Send />{action.text}</button>}{tab === "student" && ["approved", "available"].includes(String(row.status)) && <button disabled={busy} onClick={() => rowAction(row, "revoke")}><Send />Revoke</button>}</div></td></tr>; }) : <tr><td colSpan={meta.columns.length + 1}><div className="compact-empty">No records yet. Use the operational control above when prerequisites are available.</div></td></tr>}</tbody></table></div></section>}
    {auxiliary && <section className="panel"><header className="panel-header"><div><h3 className="panel-title">{auxiliary.title}</h3><p className="panel-subtitle">Related lifecycle records and controls</p></div></header><div className="table-wrap"><table><thead><tr>{auxiliary.columns.map((column) => <th key={column}>{label(column)}</th>)}<th>Action</th></tr></thead><tbody>{auxiliary.rows.length ? auxiliary.rows.map((row) => { const action = auxiliaryNext(auxiliary.title, row); return <tr key={row.id}>{auxiliary.columns.map((column) => <td key={column}>{column === "status" ? <span className={`status-pill ${row[column]}`}>{display(row[column])}</span> : display(row[column])}</td>)}<td><div className="row-actions">{action && <button disabled={busy} onClick={() => auxiliaryAction(auxiliary.title, row, action[0])}><Send />{action[1]}</button>}</div></td></tr>; }) : <tr><td colSpan={auxiliary.columns.length + 1}><div className="compact-empty">No related records yet.</div></td></tr>}</tbody></table></div></section>}
    {supportTarget && <div className="modal-backdrop"><form className="modal-panel compact" onSubmit={submitSupportRequest}><header className="modal-header"><div><h2>Request evaluator assistance</h2><p>The evaluator must approve before temporary viewer control becomes available.</p></div><button type="button" className="icon-button" title="Close" onClick={() => setSupportTarget(null)}><X /></button></header><div className="form-grid"><div className="remote-support-summary full-field"><MonitorUp /><div><strong>{display(supportTarget.evaluator)}</strong><span>{display(supportTarget.script)} · {display(supportTarget.paper)}</span></div></div><label className="field full-field"><span>Why is assistance needed?</span><textarea value={supportReason} minLength={10} onChange={(event) => setSupportReason(event.target.value)} placeholder="Describe the problem the evaluator asked you to help resolve" required /></label></div><footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setSupportTarget(null)}>Cancel</button><button className="primary-button" disabled={busy || supportReason.trim().length < 10}><MonitorUp />Send approval request</button></footer></form></div>}
    {selectedSupport && <div className="modal-backdrop"><section className="modal-panel compact"><header className="modal-header"><div><h2>Remote evaluator assistance</h2><p>{selectedSupport.evaluator} · {selectedSupport.script} · {selectedSupport.paper}</p></div><button type="button" className="icon-button" title="Close" onClick={() => setSupportSessionId("")}><X /></button></header><div className="remote-support-control"><div className={`remote-support-control-status ${selectedSupport.status}`}><span className="remote-support-live-dot" /><div><strong>{selectedSupport.status === "active" ? "Evaluator approved viewer control" : selectedSupport.status === "requested" ? "Waiting for evaluator approval" : `Session ${display(selectedSupport.status)}`}</strong><span>{selectedSupport.reason}</span></div></div>{selectedSupport.status === "active" && <><p>Controls are limited to the script viewer. Marks and submission remain exclusively with the evaluator.</p><div className="remote-support-control-buttons"><button disabled={busy} onClick={() => void sendSupportCommand(selectedSupport, "previous_page")}><ChevronLeft />Previous page</button><button disabled={busy} onClick={() => void sendSupportCommand(selectedSupport, "next_page")}>Next page<ChevronRight /></button><button disabled={busy} onClick={() => void sendSupportCommand(selectedSupport, "refresh_viewer")}><RefreshCw />Refresh viewer</button></div></>}<div className="remote-support-command-log"><strong>Recent activity</strong>{selectedSupport.commands?.length ? selectedSupport.commands.slice(-5).reverse().map((command) => <div key={command.id}><span>{display(command.kind)}</span><span className={`status-pill ${command.status}`}>{display(command.status)}</span><small>{command.result || "Waiting for evaluator screen"}</small></div>) : <span>No control actions sent.</span>}</div></div><footer className="modal-footer"><button className="danger-button" disabled={busy || !["requested", "active"].includes(String(selectedSupport.status))} onClick={() => void endSupport(selectedSupport)}>End assistance</button></footer></section></div>}
  </div>;
}
