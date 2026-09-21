"use client";

import { BadgeCheck, CalendarDays, Check, ChevronRight, CircleSlash2, CloudUpload, Copy, Ellipsis, History, Pencil, RefreshCw, ScanFace, ShieldCheck, UserRoundPlus, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";
import { DynamicFieldDefinition, DynamicFields, readDynamicFields } from "@/components/dynamic-fields";
import { IdentityStatusBadge, IdentityVerificationModal } from "@/components/evaluator-identity-verification";

type Expertise = { id: string; subject_id: string; subject_code: string; subject_name: string; level: number; years_experience: number; verified: boolean };
type Evaluator = { id: string; evaluator_code: string; display_name: string; email: string; mobile: string; employee_id: string; institution_name: string; department: string; designation: string; qualification: string; employment_type: string; years_experience: number; grade: string; status: string; daily_capacity: number; available_from: string | null; available_to: string | null; version: number; expertise: Expertise[]; availability: unknown[]; custom_fields: Record<string, unknown>; login_enabled: boolean; face_status?: string; face_enrolled?: boolean; face_enrolled_at?: string | null; face_model_version?: string; last_identity_verified_at?: string | null };
type Verification = { id: string; evaluator_id: string; evaluator: string; status: string; checks: Record<string, boolean>; expires_on: string | null; revalidation_due_on: string | null; revalidation_due: boolean; approval_count: number; required_approvals: number; fraud_signals: string[]; documents: { id: string; kind: string; status: string; sha256: string; byte_size: number; version: number }[]; version: number };
type Eligibility = { id: string; evaluator_id: string; evaluator: string; subject_id: string; subject: string; status: string; risk_reasons: string[]; expires_on: string | null; version: number };
type GovernanceHistory = { id: string; actor_id: string; action: string; aggregate_type: string; aggregate_id: string; payload: Record<string, unknown>; occurred_at: string };
type Subject = { id: string; code: string; name: string };
type HistoryRow = { id: string; action: string; reason: string; created_at: string; from_status: string; to_status: string; from_grade: string; to_grade: string };
type WorkHistory = { summary: { total: number; active: number; submitted: number; expired: number; average_quality: number | null }; assignments: { id: string; script_code: string; paper_code: string; subject: string; valuation_round: number; status: string; quality_score: number; progress_percent: number; assigned_at: string; due_at: string }[] };
type Modal = { type: "create" | "edit" | "history" | "expertise" | "availability" | "lifecycle" | "verification" | "checks" | "document" | "review" | "eligibility" | "face"; evaluator?: Evaluator; verification?: Verification } | null;

const checks = ["official_id", "university_employee", "faculty", "mobile", "email", "institutional_email", "qualification", "experience", "institution", "department", "designation", "subject_expertise", "documents", "kyc"];
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

async function request(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "The operation could not be completed");
  return body;
}

export function EvaluatorWorkspace() {
  const [view, setView] = useState<"profiles" | "verifications" | "eligibility" | "history">("profiles");
  const [evaluators, setEvaluators] = useState<Evaluator[]>([]);
  const [verifications, setVerifications] = useState<Verification[]>([]);
  const [eligibility, setEligibility] = useState<Eligibility[]>([]);
  const [governanceHistory, setGovernanceHistory] = useState<GovernanceHistory[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [customFields, setCustomFields] = useState<DynamicFieldDefinition[]>([]);
  const [credential, setCredential] = useState<{ username: string; temporary_password: string; existing_identity: boolean } | null>(null);
  const [pendingCredential, setPendingCredential] = useState<{ username: string; temporary_password: string; existing_identity: boolean } | null>(null);
  const [historyRows, setHistoryRows] = useState<HistoryRow[]>([]);
  const [workHistory, setWorkHistory] = useState<WorkHistory | null>(null);
  const [modal, setModal] = useState<Modal>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [profiles, eligibilityCatalog, config, fieldCatalog] = await Promise.all([request("/api/v1/evaluator-management"), request("/api/v1/eligibility"), request("/api/v1/configuration/catalog"), request("/api/v1/enterprise/form-fields?form_key=evaluator_profile")]);
      setEvaluators(profiles); setVerifications(eligibilityCatalog.verifications); setEligibility(eligibilityCatalog.eligibility); setGovernanceHistory(eligibilityCatalog.history || []); setSubjects(config.subjects); setCustomFields(fieldCatalog.fields || []); setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Evaluator data could not be loaded"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  async function post(path: string, payload: Record<string, unknown>, success: string) {
    setSaving(true); setError("");
    try { await request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); setModal(null); setNotice(success); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The operation could not be completed"); }
    finally { setSaving(false); }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!modal) return;
    const data = new FormData(event.currentTarget);
    if (modal.type === "create") {
      setSaving(true); setError("");
      try {
        const result = await request("/api/v1/evaluator-management", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ evaluator_code: data.get("evaluator_code"), display_name: data.get("display_name"), email: data.get("email"), mobile: data.get("mobile"), employee_id: data.get("employee_id"), institution_name: data.get("institution_name"), department: data.get("department"), designation: data.get("designation"), qualification: data.get("qualification"), employment_type: data.get("employment_type"), years_experience: Number(data.get("years_experience")), grade: data.get("grade"), daily_capacity: Number(data.get("daily_capacity")), create_login: data.get("create_login") === "on", custom_fields: readDynamicFields(data, customFields) }) });
        const createdEvaluator = {
          id: result.id,
          evaluator_code: String(data.get("evaluator_code") || result.evaluator_code || ""),
          display_name: String(data.get("display_name") || result.display_name || ""),
          email: String(data.get("email") || result.email || ""),
          mobile: String(data.get("mobile") || ""),
          employee_id: String(data.get("employee_id") || ""),
          institution_name: String(data.get("institution_name") || ""),
          department: String(data.get("department") || ""),
          designation: String(data.get("designation") || ""),
          qualification: String(data.get("qualification") || ""),
          employment_type: String(data.get("employment_type") || "permanent"),
          years_experience: Number(data.get("years_experience")),
          grade: String(data.get("grade") || "evaluator"),
          status: String(result.status || "pending"),
          daily_capacity: Number(data.get("daily_capacity")),
          available_from: null,
          available_to: null,
          version: Number(result.version || 1),
          expertise: [],
          availability: [],
          custom_fields: readDynamicFields(data, customFields),
          login_enabled: Boolean(result.login_enabled),
          face_status: "not_enrolled",
          face_enrolled: false,
          face_enrolled_at: null,
          face_model_version: "",
          last_identity_verified_at: null,
        } as Evaluator;
        if (result.face_enrollment_required !== false) {
          setNotice("Evaluator profile created. Complete face enrollment before evaluation access.");
          if (result.login_enabled) setPendingCredential(result);
          setModal({ type: "face", evaluator: createdEvaluator });
        } else {
          setNotice("Evaluator profile created.");
          setModal(null);
          if (result.login_enabled) setCredential(result);
        }
        await load();
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Evaluator could not be created"); }
      finally { setSaving(false); }
    } else if (modal.type === "edit" && modal.evaluator) {
      setSaving(true); setError("");
      try {
        await request(`/api/v1/evaluator-management/${modal.evaluator.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: modal.evaluator.version, display_name: data.get("display_name"), email: data.get("email"), mobile: data.get("mobile"), employee_id: data.get("employee_id"), institution_name: data.get("institution_name"), department: data.get("department"), designation: data.get("designation"), qualification: data.get("qualification"), employment_type: data.get("employment_type"), years_experience: Number(data.get("years_experience")), daily_capacity: Number(data.get("daily_capacity")), available_from: data.get("available_from") || null, available_to: data.get("available_to") || null, custom_fields: readDynamicFields(data, customFields) }) });
        setModal(null); setNotice("Evaluator profile updated"); await load();
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Evaluator update failed"); }
      finally { setSaving(false); }
    } else if (modal.type === "expertise" && modal.evaluator) {
      await post(`/api/v1/evaluator-management/${modal.evaluator.id}/expertise`, { subject_id: data.get("subject_id"), level: Number(data.get("level")), years_experience: Number(data.get("years_experience")) }, "Subject expertise added");
    } else if (modal.type === "availability" && modal.evaluator) {
      await post(`/api/v1/evaluator-management/${modal.evaluator.id}/availability`, { starts_on: data.get("starts_on"), ends_on: data.get("ends_on"), daily_capacity: Number(data.get("daily_capacity")), notes: data.get("notes") }, "Availability period added");
    } else if (modal.type === "lifecycle" && modal.evaluator) {
      await post(`/api/v1/evaluator-management/${modal.evaluator.id}/lifecycle`, { version: modal.evaluator.version, status: data.get("status"), grade: data.get("grade") || null, reason: data.get("reason") }, "Evaluator lifecycle updated");
    } else if (modal.type === "verification" && modal.evaluator) {
      const values = Object.fromEntries(checks.map((key) => [key, data.get(key) === "on"]));
      await post("/api/v1/eligibility/verifications", { evaluator_id: modal.evaluator.id, checks: values, notes: data.get("notes") }, "Verification case created");
    } else if (modal.type === "checks" && modal.verification) {
      setSaving(true); setError("");
      try {
        const values = Object.fromEntries(checks.map((key) => [key, data.get(key) === "on"]));
        await request(`/api/v1/eligibility/verifications/${modal.verification.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: modal.verification.version, checks: values, notes: data.get("notes") }) });
        setModal(null); setNotice("Verification checklist updated"); await load();
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Verification checklist could not be updated"); }
      finally { setSaving(false); }
    } else if (modal.type === "document" && modal.verification) {
      setSaving(true); setError("");
      try {
        const file = data.get("file") as File;
        const intent = await request(`/api/v1/eligibility/verifications/${modal.verification.id}/documents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: data.get("kind"), content_type: file.type, maximum_bytes: Math.max(file.size, 1) }) });
        const uploaded = await csrfFetch(intent.upload_url, { method: "PUT", headers: intent.headers, body: file });
        if (!uploaded.ok) throw new Error("Secure document storage rejected the upload");
        await request(`/api/v1/eligibility/documents/${intent.id}/finalize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: intent.version }) });
        setModal(null); setNotice("Verification document encrypted and hash-verified"); await load();
      } catch (reason) { setError(reason instanceof Error ? reason.message : "Verification document upload failed"); }
      finally { setSaving(false); }
    } else if (modal.type === "review" && modal.verification) {
      await post(`/api/v1/eligibility/verifications/${modal.verification.id}/approve`, { version: modal.verification.version, notes: data.get("notes"), expires_on: data.get("expires_on") }, "Evaluator verification approved");
    } else if (modal.type === "eligibility" && modal.evaluator) {
      await post("/api/v1/eligibility/subject-records", { evaluator_id: modal.evaluator.id, subject_id: data.get("subject_id"), expires_on: data.get("expires_on"), has_conflict: data.get("has_conflict") === "on", is_debarred: data.get("is_debarred") === "on", is_blacklisted: data.get("is_blacklisted") === "on" }, "Subject eligibility assessed");
    }
  }

  async function submitVerification(item: Verification) {
    await post(`/api/v1/eligibility/verifications/${item.id}/submit`, { version: item.version, notes: "Submitted from evaluator console" }, "Verification submitted for approval");
  }

  async function verifyExpertise(item: Expertise) {
    await post(`/api/v1/eligibility/expertise/${item.id}/verify`, { reason: "Verified by examination administration" }, "Subject expertise verified");
  }

  async function revalidate() {
    await post("/api/v1/eligibility/revalidate", {}, "Expired verification and eligibility records revalidated");
  }

  async function openHistory(evaluator: Evaluator) {
    setError(""); setWorkHistory(null); setHistoryRows([]); setModal({ type: "history", evaluator });
    try {
      const [profile, work] = await Promise.all([request(`/api/v1/evaluator-management/${evaluator.id}/history`), request(`/api/v1/evaluator-management/${evaluator.id}/work-history`)]);
      setHistoryRows(profile); setWorkHistory(work);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Evaluator history could not be loaded"); }
  }

  const modalTitle = modal?.type === "create" ? "Register evaluator" : modal?.type === "edit" ? "Edit evaluator profile" : modal?.type === "history" ? "Evaluator history" : modal?.type === "expertise" ? "Add subject expertise" : modal?.type === "availability" ? "Add availability" : modal?.type === "lifecycle" ? "Change evaluator lifecycle" : modal?.type === "verification" ? "Create verification case" : modal?.type === "checks" ? "Complete verification checks" : modal?.type === "document" ? "Upload verification document" : modal?.type === "review" ? "Record verification approval" : modal?.type === "face" ? "Face enrollment" : "Assess subject eligibility";

  return <div className="config-workspace">
    <div className="workspace-toolbar"><div className="entity-tabs"><button className={view === "profiles" ? "active" : ""} onClick={() => setView("profiles")}>Profiles</button><button className={view === "verifications" ? "active" : ""} onClick={() => setView("verifications")}>Verification</button><button className={view === "eligibility" ? "active" : ""} onClick={() => setView("eligibility")}>Subject eligibility</button><button className={view === "history" ? "active" : ""} onClick={() => setView("history")}>History</button></div>{view === "profiles" ? <button className="primary-button" onClick={() => { setModal({ type: "create" }); setError(""); }}><UserRoundPlus />Register evaluator</button> : view === "verifications" ? <button className="secondary-button" onClick={revalidate}><RefreshCw />Revalidate expiry</button> : null}</div>
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && !modal && <div className="form-error">{error}</div>}
    <section className="panel">{loading ? <div className="empty-state">Loading evaluator records…</div> : view === "profiles" ? <div className="table-wrap"><table><thead><tr><th>Evaluator</th><th>Institution</th><th>Role</th><th>Status</th><th>Identity</th><th>Capacity</th><th>Expertise</th><th>Actions</th></tr></thead><tbody>{evaluators.map((item) => <tr key={item.id}><td><div className="person-cell"><strong>{item.display_name}</strong><span>{item.evaluator_code} · {item.email || "No email"}</span></div></td><td><div className="person-cell"><strong>{item.institution_name}</strong><span>{item.department}</span></div></td><td>{titleCase(item.grade)}</td><td><span className={`status-pill ${item.status}`}>{titleCase(item.status)}</span></td><td><IdentityStatusBadge evaluator={item} /></td><td>{item.daily_capacity}/day</td><td><div className="expertise-list">{item.expertise.length ? item.expertise.map((skill) => <button className={skill.verified ? "verified" : ""} title={skill.verified ? "Verified expertise" : "Verify expertise"} onClick={() => !skill.verified && verifyExpertise(skill)} key={skill.id}>{skill.subject_code}{skill.verified && <BadgeCheck />}</button>) : <span>None</span>}</div></td><td><EvaluatorActions evaluator={item} verifications={verifications} open={(next) => setModal({ type: next, evaluator: item })} history={() => void openHistory(item)} /></td></tr>)}</tbody></table></div> : view === "verifications" ? <div className="table-wrap"><table><thead><tr><th>Evaluator</th><th>Status</th><th>Checks</th><th>Evidence</th><th>Approvals</th><th>Expiry</th><th>Action</th></tr></thead><tbody>{verifications.map((item) => { const completed = checks.filter((key) => item.checks[key]).length; const evidenceReady = item.documents.some((document) => document.status === "completed"); const ready = completed === checks.length && evidenceReady && item.fraud_signals.length === 0; return <tr key={item.id}><td><div className="person-cell"><strong>{item.evaluator}</strong><span>{item.fraud_signals.length ? item.fraud_signals.map(titleCase).join(", ") : "No profile risk signals"}</span></div></td><td><span className={`status-pill ${item.status}`}>{titleCase(item.status)}</span></td><td><strong>{completed}/{checks.length}</strong>{item.status === "draft" && completed < checks.length && <small>{checks.length - completed} remaining</small>}</td><td>{item.documents.length ? `${item.documents.filter((document) => document.status === "completed").length} document(s) verified` : item.status === "approved" ? "Historic evidence" : "None"}</td><td>{item.approval_count}/{item.required_approvals}</td><td>{item.expires_on || "—"}{item.revalidation_due && <span className="desk-flag">Revalidate</span>}</td><td><div className="row-actions">{item.status === "draft" && <button title="Complete verification checks" onClick={() => setModal({ type: "checks", verification: item })}><Pencil /></button>}{item.status === "draft" && <button title="Upload evidence" onClick={() => setModal({ type: "document", verification: item })}><CloudUpload /></button>}{item.status === "draft" && <button title={ready ? "Submit for approval" : "Complete all checks, upload evidence, and resolve risk signals before submitting"} disabled={!ready} onClick={() => submitVerification(item)}>Submit</button>}{["submitted", "in_review"].includes(item.status) && <button onClick={() => setModal({ type: "review", verification: item })}>Review</button>}{item.status === "approved" && <span className="locked-label"><BadgeCheck />Verified</span>}</div></td></tr>; })}</tbody></table></div> : view === "eligibility" ? <div className="table-wrap"><table><thead><tr><th>Evaluator</th><th>Subject</th><th>Status</th><th>Valid until</th><th>Risk reasons</th></tr></thead><tbody>{eligibility.map((item) => <tr key={item.id}><td>{item.evaluator}</td><td>{item.subject}</td><td><span className={`status-pill ${item.status}`}>{titleCase(item.status)}</span></td><td>{item.expires_on || "—"}</td><td>{item.risk_reasons.length ? item.risk_reasons.map(titleCase).join(", ") : "None"}</td></tr>)}</tbody></table></div> : <div className="table-wrap"><table><thead><tr><th>Event</th><th>Record</th><th>Actor</th><th>Evidence</th><th>Time</th></tr></thead><tbody>{governanceHistory.map((item) => <tr key={item.id}><td><strong>{titleCase(item.action.replace("eligibility.", ""))}</strong></td><td>{item.aggregate_type}<br /><small>{item.aggregate_id.slice(0, 12)}</small></td><td>{item.actor_id}</td><td>{Object.entries(item.payload).slice(0, 3).map(([key, value]) => `${titleCase(key)}: ${String(value)}`).join(" · ") || "Recorded"}</td><td>{new Date(item.occurred_at).toLocaleString("en-IN")}</td></tr>)}</tbody></table></div>}</section>
    {modal?.type === "face" && modal.evaluator && <IdentityVerificationModal mode="enroll" evaluator={modal.evaluator} onClose={() => { setModal(null); if (pendingCredential) { setCredential(pendingCredential); setPendingCredential(null); } }} onComplete={async () => { setModal(null); setNotice("Face template enrolled and bound to evaluator access"); if (pendingCredential) { setCredential(pendingCredential); setPendingCredential(null); } await load(); }} />}
    {modal && modal.type !== "face" && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{modalTitle}</h2><p>{modal.evaluator?.display_name || modal.verification?.evaluator || "Evaluator master"}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header>{modal.type === "history" ? <EvaluatorHistory history={historyRows} work={workHistory} error={error} /> : <form onSubmit={submit}><div className="form-grid">
      {modal.type === "create" && <><Field name="evaluator_code" label="Evaluator ID" /><Field name="display_name" label="Full name" /><Field name="email" label="Institutional email" type="email" /><Field name="mobile" label="Mobile" /><Field name="employee_id" label="Employee ID" /><Field name="institution_name" label="Institution" /><Field name="department" label="Department" /><Field name="designation" label="Designation" /><Field name="qualification" label="Qualification" /><Select name="employment_type" label="Employment" options={["permanent", "contract", "visiting"]} /><Field name="years_experience" label="Experience in years" type="number" /><Select name="grade" label="Evaluator role" options={["evaluator", "senior", "chief", "moderator", "revaluator", "scrutinizer", "verifier"]} /><Field name="daily_capacity" label="Daily capacity" type="number" defaultValue="20" /><label className="field check-field full-field"><input name="create_login" type="checkbox" defaultChecked /><span>Create Evaluation module login and issue a temporary password</span></label><DynamicFields fields={customFields} /></>}
      {modal.type === "edit" && modal.evaluator && <><Field name="display_name" label="Full name" defaultValue={modal.evaluator.display_name} /><Field name="email" label="Institutional email" type="email" defaultValue={modal.evaluator.email} /><Field name="mobile" label="Mobile" defaultValue={modal.evaluator.mobile} /><Field name="employee_id" label="Employee ID" defaultValue={modal.evaluator.employee_id} /><Field name="institution_name" label="Institution" defaultValue={modal.evaluator.institution_name} /><Field name="department" label="Department" defaultValue={modal.evaluator.department} /><Field name="designation" label="Designation" defaultValue={modal.evaluator.designation} /><Field name="qualification" label="Qualification" defaultValue={modal.evaluator.qualification} /><Select name="employment_type" label="Employment" options={["permanent", "contract", "visiting"]} defaultValue={modal.evaluator.employment_type} /><Field name="years_experience" label="Experience in years" type="number" defaultValue={String(modal.evaluator.years_experience)} /><Field name="daily_capacity" label="Daily capacity" type="number" defaultValue={String(modal.evaluator.daily_capacity)} /><Field name="available_from" label="Generally available from" type="date" defaultValue={modal.evaluator.available_from || ""} required={false} /><Field name="available_to" label="Generally available to" type="date" defaultValue={modal.evaluator.available_to || ""} required={false} /><DynamicFields fields={customFields} values={modal.evaluator.custom_fields} /></>}
      {modal.type === "expertise" && <><Select name="subject_id" label="Subject" options={subjects.map((item) => item.id)} optionLabels={subjects.map((item) => `${item.code} · ${item.name}`)} /><Field name="level" label="Expertise level (1-5)" type="number" /><Field name="years_experience" label="Subject experience" type="number" /></>}
      {modal.type === "availability" && <><Field name="starts_on" label="Available from" type="date" /><Field name="ends_on" label="Available to" type="date" /><Field name="daily_capacity" label="Daily capacity" type="number" /><Field name="notes" label="Notes" /></>}
      {modal.type === "lifecycle" && <><Select name="status" label="New status" options={["active", "inactive", "suspended", "retired"]} /><Select name="grade" label="New role (optional)" options={["", "evaluator", "senior", "chief", "moderator", "revaluator", "scrutinizer", "verifier"]} /><Field name="reason" label="Reason" /></>}
      {modal.type === "verification" && <><div className="verification-grid">{checks.map((key) => <label className="check-field" key={key}><input type="checkbox" name={key} /><span>{titleCase(key)}</span></label>)}</div><Field name="notes" label="Verification notes" /></>}
      {modal.type === "checks" && modal.verification && <><div className="verification-grid">{checks.map((key) => <label className="check-field" key={key}><input type="checkbox" name={key} defaultChecked={Boolean(modal.verification?.checks[key])} /><span>{titleCase(key)}</span></label>)}</div><Field name="notes" label="Verification notes" required={false} /></>}
      {modal.type === "document" && <><label className="field"><span>Evidence type</span><select name="kind" defaultValue="official_id"><option value="official_id">Official identity</option><option value="qualification">Qualification</option><option value="employment">Employment</option><option value="kyc">KYC evidence</option></select></label><label className="field file-field"><span>PDF or image</span><input name="file" type="file" accept="application/pdf,image/jpeg,image/png" required /></label></>}
      {modal.type === "review" && <><Field name="expires_on" label="Verification expires" type="date" /><Field name="notes" label="Approval note" /></>}
      {modal.type === "eligibility" && <><Select name="subject_id" label="Subject" options={subjects.map((item) => item.id)} optionLabels={subjects.map((item) => `${item.code} · ${item.name}`)} /><Field name="expires_on" label="Eligibility expires" type="date" /><label className="check-field"><input type="checkbox" name="has_conflict" /><span>Conflict of interest</span></label><label className="check-field"><input type="checkbox" name="is_debarred" /><span>Debarred evaluator</span></label><label className="check-field"><input type="checkbox" name="is_blacklisted" /><span>Blacklisted</span></label></>}
    </div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>{saving ? "Saving…" : "Save"}<ChevronRight /></button></footer></form>}</div></div>}
    {credential && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Evaluator login ready</h2><p>Share these details through an approved secure channel.</p></div><button className="icon-button" title="Close" onClick={() => setCredential(null)}><X /></button></header><div className="credential-sheet"><label><span>User ID</span><code>{credential.username}</code></label>{credential.temporary_password ? <label><span>Temporary password</span><code>{credential.temporary_password}</code><button className="icon-button" title="Copy credentials" onClick={() => navigator.clipboard.writeText(`User ID: ${credential.username}\nTemporary password: ${credential.temporary_password}`)}><Copy /></button></label> : <p>This identity already existed, so its current password remains unchanged.</p>}<small>The evaluator must set a new password at first sign-in.</small></div><footer className="modal-footer"><button className="primary-button" onClick={() => setCredential(null)}>Done</button></footer></div></div>}
  </div>;
}

function Field({ name, label, type = "text", defaultValue, required = true }: { name: string; label: string; type?: string; defaultValue?: string; required?: boolean }) {
  return <label className="field"><span>{label}</span><input name={name} type={type} defaultValue={defaultValue} min={type === "number" ? 0 : undefined} required={required} /></label>;
}

function Select({ name, label, options, optionLabels, defaultValue }: { name: string; label: string; options: string[]; optionLabels?: string[]; defaultValue?: string }) {
  return <label className="field"><span>{label}</span><select name={name} required={options[0] !== ""} defaultValue={defaultValue ?? (options[0] === "" ? "" : undefined)}>{options.map((option, index) => <option value={option} key={`${name}-${option}`}>{optionLabels?.[index] || (option ? titleCase(option) : "No change")}</option>)}</select></label>;
}

function EvaluatorHistory({ history, work, error }: { history: HistoryRow[]; work: WorkHistory | null; error: string }) {
  if (error) return <div className="form-error">{error}</div>;
  if (!work) return <div className="empty-state">Loading evaluator history…</div>;
  return <div className="evaluator-history"><div className="run-metrics"><div><span>Total assignments</span><strong>{work.summary.total}</strong></div><div><span>Active / submitted</span><strong>{work.summary.active} / {work.summary.submitted}</strong></div><div><span>Average quality</span><strong>{work.summary.average_quality === null ? "—" : `${Math.round(work.summary.average_quality)}%`}</strong></div></div><div className="table-wrap"><table><thead><tr><th>Anonymous script</th><th>Paper / subject</th><th>Round</th><th>Status</th><th>Progress</th><th>Assigned</th></tr></thead><tbody>{work.assignments.map((item) => <tr key={item.id}><td><strong>{item.script_code}</strong></td><td>{item.paper_code}<br /><small>{item.subject}</small></td><td>{item.valuation_round}</td><td><span className={`status-pill ${item.status}`}>{titleCase(item.status)}</span></td><td>{item.progress_percent}%</td><td>{new Date(item.assigned_at).toLocaleDateString("en-IN")}</td></tr>)}</tbody></table></div>{!work.assignments.length && <div className="empty-state">No assignments recorded for this evaluator.</div>}<header className="subsection-heading"><strong>Profile and lifecycle record</strong><span>Immutable administrative history</span></header><div className="history-list">{history.map((item) => <div className="history-item" key={item.id}><div><History /><strong>{titleCase(item.action)}</strong>{item.to_status && <span>{titleCase(item.to_status)}</span>}</div><p>{item.reason || [item.from_grade, item.to_grade].filter(Boolean).map(titleCase).join(" to ") || "Profile snapshot recorded"}</p><small>{new Date(item.created_at).toLocaleString("en-IN")}</small></div>)}</div>{!history.length && <div className="compact-empty">No profile changes have been recorded.</div>}</div>;
}

function EvaluatorActions({ evaluator, verifications, open, history }: { evaluator: Evaluator; verifications: Verification[]; open: (type: "edit" | "expertise" | "availability" | "lifecycle" | "verification" | "eligibility" | "face") => void; history: () => void }) {
  const verification = verifications.find((item) => item.evaluator_id === evaluator.id);
  return <details className="row-menu"><summary title="Evaluator actions"><Ellipsis /></summary><div><button onClick={() => open("edit")}><Pencil />Edit profile</button><button onClick={history}><History />Work history</button><button onClick={() => open("expertise")}><BadgeCheck />Expertise</button><button onClick={() => open("availability")}><CalendarDays />Availability</button><button onClick={() => open("lifecycle")}><CircleSlash2 />Lifecycle</button><button onClick={() => open("face")}><ScanFace />Face enrollment</button>{!verification && <button onClick={() => open("verification")}><ShieldCheck />Verification</button>}{verification?.status === "approved" && <button onClick={() => open("eligibility")}><ShieldCheck />Eligibility</button>}</div></details>;
}
