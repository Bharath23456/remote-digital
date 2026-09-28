"use client";

import {
  AlertTriangle, Check, Fingerprint, KeyRound, Laptop, LockKeyhole, Plus,
  Copy, RefreshCw, ShieldAlert, ShieldCheck, Smartphone, UserCog, UserPlus, X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { createPasskey } from "@/lib/webauthn";
import { csrfFetch } from "@/lib/api";
import { DynamicFieldDefinition, DynamicFields, readDynamicFields } from "@/components/dynamic-fields";

type Session = { id: string; device: string; ip_address: string | null; risk_score: number; last_seen_at: string; expires_at: string; revoked_at: string | null; status: "active" | "expired" | "revoked"; step_up_valid: boolean };
type Device = { id: string; label: string; platform: string; browser: string; trusted_until: string | null; last_seen_at: string | null; revoked_at: string | null };
type ManagedDevice = { id: string; user: string; email: string; label: string; platform: string; last_seen_at: string | null; status: "pending" | "approved" | "revoked" };
type Method = { id: string; kind: string; label: string; active: boolean; verified_at: string | null };
type History = { id: string; event: string; outcome: string; ip_address: string | null; risk_score: number; risk_reasons: string[]; created_at: string };
type PersonalContext = { current_session_id: string | null; server_time: string; totals: { sessions: number; devices: number; history: number }; policy: Policy; sessions: Session[]; devices: Device[]; methods: Method[]; history: History[] };
type Policy = { version: number; session_timeout_minutes: number; maximum_concurrent_sessions: number; step_up_minutes: number; failed_login_limit: number; lockout_minutes: number; require_mfa: boolean; require_trusted_device: boolean; approved_networks: string[]; allowed_countries: string[]; vpn_risk_threshold: number; alert_risk_threshold: number; dlp_enabled: boolean; evaluation_strict_mode: boolean; evaluation_identity_verification_required: boolean; evaluation_camera_required: boolean; evaluation_fullscreen_required: boolean; evaluation_single_screen_required: boolean; evaluation_mobile_allowed: boolean; evaluation_event_recording: boolean; evaluation_pause_on_violation: boolean; evaluation_require_resume_step_up: boolean; evaluation_allow_clipboard: boolean; evaluation_allow_download: boolean; evaluation_allow_print: boolean; evaluation_session_timeout_minutes: number; evaluation_heartbeat_seconds: number; evaluation_no_face_seconds: number; evaluation_retention_days: number; ai_evaluation_mode: "disabled" | "assistive" | "autonomous"; ai_confidence_threshold: number; ai_model_name: string };
type Alert = { id: string; category: string; severity: string; title: string; status: string; version: number; created_at: string };
type AccessRequest = { id: string; requester: string; requested_role: string; reason: string; status: string; expires_at: string | null; decided_by: string | null; version: number };
type EmergencyGrant = { id: string; user: string; role: string; incident_reference: string; expires_at: string; revoked_at: string | null };
type DlpIncident = { id: string; channel: string; data_classification: string; rule: string; resource_reference: string; status: string; created_at: string };
type Member = { id: string; user_id: number; name: string; email: string; institution: string; role: string; permissions: string[]; enabled_modules: string[]; custom_fields: Record<string, unknown>; is_active: boolean; authenticator_enabled: boolean; can_reset_authenticator: boolean };
type Provider = { id: string; name: string; issuer: string; client_id: string; scopes: string; domain_hint: string; is_active: boolean };
type AIProvider = { provider: string; configured: boolean; valid: boolean; available: boolean; message: string };
type Catalog = { policy: Policy; ai_provider: AIProvider; devices: ManagedDevice[]; alerts: Alert[]; access_requests: AccessRequest[]; emergency_grants: EmergencyGrant[]; dlp_incidents: DlpIncident[]; members: Member[]; oidc_providers: Provider[]; key_inventory: Record<string, unknown>[]; controls: Record<string, boolean>; module_catalog: string[] };
type Tab = "personal" | "policy" | "evaluation_security" | "access" | "alerts" | "infrastructure";
type Modal = "step-up" | "totp" | "request" | "emergency" | "oidc" | "member" | "create-member" | "reset-authenticator" | null;

const titleCase = (value: string) => value === "personal" ? "My sign-in" : value === "evaluation_security" ? "Evaluation security" : value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const moduleLabel: Record<string, string> = { receiving: "Bundle preparation", custody: "Chain of custody", digitization: "Digitization", ai_evaluation: "AI evaluation", operations: "Live operations" };
const formatDate = (value: string | null) => value ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
const fixedRoleModules: Record<string, string[]> = {
  evaluator: ["evaluation"],
  receiving_officer: ["receiving"],
  script_receiver: ["receiving"],
  scanner_operator: ["digitization"],
  custody_officer: ["custody"],
  bundle_preparer: ["receiving"],
  intake_receiver: ["custody"],
  scan_operator: ["digitization"],
  operations_supervisor: ["receiving", "custody", "digitization"],
  auditor: ["audit"],
};
const modulesForRole = (role: string, available: string[]) => fixedRoleModules[role]?.filter((module) => available.includes(module)) || available;

async function api(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "The security operation could not be completed");
  return body;
}

export function SecurityWorkspace() {
  const [tab, setTab] = useState<Tab>("personal");
  const [personal, setPersonal] = useState<PersonalContext | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [modal, setModal] = useState<Modal>(null);
  const [afterStepUpModal, setAfterStepUpModal] = useState<Modal>(null);
  const [selectedMember, setSelectedMember] = useState<Member | null>(null);
  const [totp, setTotp] = useState<{ method_id: string; secret: string; provisioning_uri: string } | null>(null);
  const [customFields, setCustomFields] = useState<DynamicFieldDefinition[]>([]);
  const [credential, setCredential] = useState<{ email: string; temporary_password: string; existing_identity: boolean } | null>(null);
  const [newMemberRole, setNewMemberRole] = useState("exam_controller");
  const [editMemberRole, setEditMemberRole] = useState({ id: "", role: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [clockDriftSeconds, setClockDriftSeconds] = useState(0);
  const [expandedPersonal, setExpandedPersonal] = useState({ sessions: false, devices: false, history: false });
  const [expandedManagedDevices, setExpandedManagedDevices] = useState(false);

  const load = useCallback(async () => {
    try {
      const [personalData, catalogData, fieldCatalog] = await Promise.all([
        api("/api/v1/auth/security-context"),
        api("/api/v1/security/catalog"),
        api("/api/v1/enterprise/form-fields?form_key=user_access"),
      ]);
      setPersonal(personalData); setCatalog(catalogData); setCustomFields(fieldCatalog.fields || []);
      setClockDriftSeconds(Math.round(Math.abs(Date.now() - new Date(personalData.server_time).getTime()) / 1000));
      setError("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Security controls could not be loaded"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  const currentSession = useMemo(() => personal?.sessions.find((item) => item.id === personal.current_session_id), [personal]);
  const stepUpValid = Boolean(currentSession?.step_up_valid);
  const hasActiveTotp = Boolean(personal?.methods.some((item) => item.kind === "totp" && item.active));
  const personalRows = personal ? {
    sessions: personal.sessions.slice(0, expandedPersonal.sessions ? 20 : 5),
    devices: personal.devices.slice(0, expandedPersonal.devices ? 50 : 5),
    history: personal.history.slice(0, expandedPersonal.history ? 50 : 5),
  } : { sessions: [], devices: [], history: [] };
  const managedDevices = catalog ? [...catalog.devices].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending")) : [];
  const managedDeviceRows = managedDevices.slice(0, expandedManagedDevices ? 100 : 10);
  function togglePersonal(section: keyof typeof expandedPersonal) {
    setExpandedPersonal((current) => ({ ...current, [section]: !current[section] }));
  }
  function requireStepUp(nextModal: Modal = null) {
    if (stepUpValid) return true;
    setAfterStepUpModal(nextModal);
    setModal("step-up"); setError("");
    return false;
  }

  async function stepUp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      await api("/api/v1/auth/step-up", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: data.get("password"), code: data.get("code") }) });
      setModal(afterStepUpModal); setAfterStepUpModal(null); setNotice("Privileged actions unlocked for this session"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Re-authentication failed"); }
    finally { setSaving(false); }
  }

  async function beginTotp() {
    if (hasActiveTotp && !requireStepUp()) return;
    setSaving(true); setError("");
    try { setTotp(await api("/api/v1/auth/totp/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: "Primary authenticator" }) })); setModal("totp"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Authenticator setup could not start"); }
    finally { setSaving(false); }
  }

  async function confirmTotp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!totp) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      await api("/api/v1/auth/totp/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ method_id: totp.method_id, code: data.get("code") }) });
      setModal(null); setTotp(null); setNotice("Authenticator MFA is active"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Authenticator code was rejected"); }
    finally { setSaving(false); }
  }

  async function registerPasskey() {
    setSaving(true); setError("");
    try {
      const options = await api("/api/v1/auth/passkeys/registration/options", { method: "POST" });
      const credential = await createPasskey(options);
      await api("/api/v1/auth/passkeys/registration/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: "Platform passkey", credential }) });
      setNotice("Passkey registered for phishing-resistant sign-in"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Passkey registration failed"); }
    finally { setSaving(false); }
  }

  async function deviceAction(deviceId: string) {
    if (!requireStepUp()) return; setSaving(true); setError("");
    try {
      await api(`/api/v1/auth/devices/${deviceId}/revoke`, { method: "POST" });
      setNotice("Device and its sessions revoked"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Device action failed"); }
    finally { setSaving(false); }
  }

  async function manageDevice(deviceId: string, action: "approve" | "revoke") {
    if (!requireStepUp()) return; setSaving(true); setError("");
    try {
      await api(`/api/v1/security/devices/${deviceId}/${action}`, { method: "POST" });
      setNotice(action === "approve" ? "Device approved for this university" : "Device access revoked and sessions ended"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Device access could not be changed"); }
    finally { setSaving(false); }
  }

  async function revokeSession(sessionId: string) {
    if (!requireStepUp()) return; setSaving(true); setError("");
    try { await api(`/api/v1/auth/sessions/${sessionId}/revoke`, { method: "POST" }); setNotice("Session revoked"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Session could not be revoked"); }
    finally { setSaving(false); }
  }

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!catalog || !requireStepUp()) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    const number = (name: string, fallback: number) => data.get(name) === null ? fallback : Number(data.get(name));
    const bool = (name: string, fallback: boolean) => data.get(name) === null ? fallback : data.get(name) === "on";
    const list = (name: string, fallback: string[]) => data.get(name) === null ? fallback : String(data.get(name) || "").split(",").map((item) => item.trim()).filter(Boolean);
    const payload = { ...catalog.policy,
      session_timeout_minutes: number("session_timeout_minutes", catalog.policy.session_timeout_minutes), maximum_concurrent_sessions: number("maximum_concurrent_sessions", catalog.policy.maximum_concurrent_sessions),
      step_up_minutes: number("step_up_minutes", catalog.policy.step_up_minutes), failed_login_limit: number("failed_login_limit", catalog.policy.failed_login_limit), lockout_minutes: number("lockout_minutes", catalog.policy.lockout_minutes),
      require_mfa: bool("require_mfa", catalog.policy.require_mfa), require_trusted_device: bool("require_trusted_device", catalog.policy.require_trusted_device),
      approved_networks: list("approved_networks", catalog.policy.approved_networks), allowed_countries: list("allowed_countries", catalog.policy.allowed_countries),
      vpn_risk_threshold: number("vpn_risk_threshold", catalog.policy.vpn_risk_threshold), alert_risk_threshold: number("alert_risk_threshold", catalog.policy.alert_risk_threshold), dlp_enabled: bool("dlp_enabled", catalog.policy.dlp_enabled),
      evaluation_camera_required: bool("evaluation_camera_required", catalog.policy.evaluation_camera_required),
      evaluation_fullscreen_required: bool("evaluation_fullscreen_required", catalog.policy.evaluation_fullscreen_required),
      evaluation_single_screen_required: bool("evaluation_single_screen_required", catalog.policy.evaluation_single_screen_required),
      evaluation_strict_mode: bool("evaluation_strict_mode", catalog.policy.evaluation_strict_mode),
      evaluation_identity_verification_required: bool("evaluation_identity_verification_required", catalog.policy.evaluation_identity_verification_required),
      evaluation_mobile_allowed: bool("evaluation_mobile_allowed", catalog.policy.evaluation_mobile_allowed),
      evaluation_event_recording: bool("evaluation_event_recording", catalog.policy.evaluation_event_recording),
      evaluation_pause_on_violation: bool("evaluation_pause_on_violation", catalog.policy.evaluation_pause_on_violation),
      evaluation_require_resume_step_up: bool("evaluation_require_resume_step_up", catalog.policy.evaluation_require_resume_step_up),
      evaluation_allow_clipboard: bool("evaluation_allow_clipboard", catalog.policy.evaluation_allow_clipboard),
      evaluation_allow_download: bool("evaluation_allow_download", catalog.policy.evaluation_allow_download),
      evaluation_allow_print: bool("evaluation_allow_print", catalog.policy.evaluation_allow_print),
      evaluation_session_timeout_minutes: number("evaluation_session_timeout_minutes", catalog.policy.evaluation_session_timeout_minutes),
      evaluation_heartbeat_seconds: number("evaluation_heartbeat_seconds", catalog.policy.evaluation_heartbeat_seconds),
      evaluation_no_face_seconds: number("evaluation_no_face_seconds", catalog.policy.evaluation_no_face_seconds),
      evaluation_retention_days: number("evaluation_retention_days", catalog.policy.evaluation_retention_days),
      ai_evaluation_mode: data.get("ai_evaluation_mode") === null ? catalog.policy.ai_evaluation_mode : String(data.get("ai_evaluation_mode")),
      ai_confidence_threshold: number("ai_confidence_threshold", catalog.policy.ai_confidence_threshold),
      ai_model_name: String(data.get("ai_model_name") || catalog.policy.ai_model_name),
    };
    try { await api("/api/v1/security/policy", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); setNotice(tab === "evaluation_security" ? "Evaluation security policy updated" : "Tenant security policy updated"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Security policy could not be saved"); }
    finally { setSaving(false); }
  }

  async function requestAccess(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError(""); const data = new FormData(event.currentTarget);
    try { await api("/api/v1/security/privileged-access", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ requested_role: data.get("requested_role"), reason: data.get("reason"), duration_minutes: Number(data.get("duration_minutes")) }) }); setModal(null); setNotice("Privileged access request submitted for independent approval"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Access request failed"); }
    finally { setSaving(false); }
  }

  async function decideAccess(item: AccessRequest, approve: boolean) {
    if (!requireStepUp()) return; setSaving(true); setError("");
    try { await api(`/api/v1/security/privileged-access/${item.id}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: item.version, approve, note: approve ? "Approved from governance console" : "Rejected from governance console" }) }); setNotice(`Access request ${approve ? "approved" : "rejected"}`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Access decision failed"); }
    finally { setSaving(false); }
  }

  async function changeAlertStatus(item: Alert, status: "investigating" | "resolved") {
    setSaving(true); setError("");
    try { await api(`/api/v1/security/alerts/${item.id}/status`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: item.version, status }) }); setNotice(`Alert marked ${status}`); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Alert status could not be changed"); }
    finally { setSaving(false); }
  }

  async function grantEmergency(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!requireStepUp()) return; setSaving(true); setError(""); const data = new FormData(event.currentTarget);
    try { await api("/api/v1/security/emergency-access", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: Number(data.get("user_id")), role: data.get("role"), incident_reference: data.get("incident_reference"), justification: data.get("justification"), duration_minutes: Number(data.get("duration_minutes")) }) }); setModal(null); setNotice("Time-bound emergency access granted"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Emergency access could not be granted"); }
    finally { setSaving(false); }
  }

  async function revokeEmergency(grant: EmergencyGrant) {
    if (!requireStepUp()) return; setSaving(true); setError("");
    try { await api(`/api/v1/security/emergency-access/${grant.id}/revoke`, { method: "POST" }); setNotice("Emergency access revoked"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Emergency access could not be revoked"); }
    finally { setSaving(false); }
  }

  async function saveMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selectedMember || !requireStepUp()) return; setSaving(true); setError(""); const data = new FormData(event.currentTarget);
    const role = String(data.get("role"));
    try { await api(`/api/v1/security/memberships/${selectedMember.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ role, permissions: role === "operations_supervisor" ? [] : String(data.get("permissions") || "").split(",").map((item) => item.trim()).filter(Boolean), enabled_modules: data.getAll("modules"), is_active: data.get("is_active") === "on" }) }); setModal(null); setSelectedMember(null); setNotice("Role and module access updated"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Membership could not be updated"); }
    finally { setSaving(false); }
  }

  async function resetAuthenticator() {
    if (!selectedMember || !requireStepUp("reset-authenticator")) return;
    setSaving(true); setError("");
    try {
      await api(`/api/v1/security/memberships/${selectedMember.id}/reset-authenticator`, { method: "POST" });
      setModal(null); setSelectedMember(null);
      setNotice("Authenticator reset. The user's sessions ended; their next password sign-in will show a new QR code when MFA is required.");
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Authenticator could not be reset"); }
    finally { setSaving(false); }
  }

  async function createMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!requireStepUp()) return; setSaving(true); setError(""); const data = new FormData(event.currentTarget);
    try {
      const created = await api("/api/v1/security/memberships", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ first_name: data.get("first_name"), last_name: data.get("last_name"), email: data.get("email"), role: data.get("role"), permissions: [], enabled_modules: data.getAll("modules"), custom_fields: readDynamicFields(data, customFields) }) });
      setModal(null); setCredential(created); setNotice("User access created"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "User could not be created"); }
    finally { setSaving(false); }
  }

  async function createProvider(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!requireStepUp()) return; setSaving(true); setError(""); const data = new FormData(event.currentTarget);
    try { await api("/api/v1/security/oidc-providers", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(data.entries())) }); setModal(null); setNotice("Institutional SSO provider configured"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "SSO provider could not be configured"); }
    finally { setSaving(false); }
  }

  if (loading) return <div className="empty-state"><RefreshCw className="spin" /></div>;
  if (!personal || !catalog) return <div className="form-error">{error || "Security controls are unavailable"}</div>;

  return <div className="config-workspace">
    <div className="security-status-band"><div><ShieldCheck /><span>Session protection</span><strong>{personal.policy.session_timeout_minutes} min</strong></div><div><Fingerprint /><span>Authentication</span><strong>{personal.methods.some((item) => item.active) ? "MFA ready" : "Password only"}</strong></div><div><ShieldAlert /><span>Open alerts</span><strong>{catalog.alerts.filter((item) => item.status !== "resolved").length}</strong></div><button className={stepUpValid ? "status-pill ready" : "secondary-button"} onClick={() => setModal("step-up")}><LockKeyhole />{stepUpValid ? "Step-up active" : "Re-authenticate"}</button></div>
     <div className="workspace-toolbar"><div className="entity-tabs" role="tablist">{(["personal", "policy", "evaluation_security", "access", "alerts", "infrastructure"] as Tab[]).map((item) => <button className={tab === item ? "active" : ""} onClick={() => setTab(item)} key={item}>{titleCase(item)}</button>)}</div></div>
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && !modal && <div className="form-error"><AlertTriangle />{error}</div>}

    {tab === "personal" && <p className="panel-subtitle">Authenticator and passkey settings below apply only to your account. Policy controls university-wide requirements.</p>}
     {tab === "policy" && <p className="panel-subtitle">Authentication, trusted devices, network access and DLP controls for the university.</p>}
     {tab === "evaluation_security" && <p className="panel-subtitle">Dedicated evaluator-session controls: identity checks, device restrictions, monitoring, evidence and interruption handling.</p>}
    {tab === "access" && !catalog.policy.require_trusted_device && <p className="panel-subtitle">Device approval is currently optional for sign-in. Turn on Require trusted device in Policy to enforce it for everyone.</p>}
    {tab === "access" && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">University devices</h2><p className="panel-subtitle">{managedDevices.filter((item) => item.status === "pending").length} pending approvals. Revoking access ends active sessions and blocks re-login.</p></div>{managedDevices.length > 10 && <button className="text-button" onClick={() => setExpandedManagedDevices((value) => !value)}>{expandedManagedDevices ? "Show first 10" : `View recent ${managedDevices.length}`}</button>}</header><div className="table-wrap"><table><thead><tr><th>User</th><th>Device</th><th>Last seen</th><th>Status</th><th>Action</th></tr></thead><tbody>{managedDeviceRows.map((item) => <tr key={item.id}><td><strong>{item.user}</strong><br /><small>{item.email}</small></td><td>{item.label}<br /><small>{item.platform}</small></td><td>{formatDate(item.last_seen_at)}</td><td><span className={`status-pill ${item.status === "approved" ? "active" : "attention"}`}>{titleCase(item.status)}</span></td><td><div className="row-actions">{item.status === "pending" && <button disabled={saving} onClick={() => manageDevice(item.id, "approve")}>Approve</button>}{item.status !== "revoked" && <button className="danger-text" disabled={saving} onClick={() => manageDevice(item.id, "revoke")}>Revoke</button>}</div></td></tr>)}</tbody></table></div>{!managedDevices.length && <div className="empty-state">No devices have been registered for this university.</div>}</section>}

    {tab === "personal" && <div className="security-stack"><section className="panel"><header className="panel-header"><div><h2 className="panel-title">Authentication methods</h2><p className="panel-subtitle">Methods tied to your institutional account</p></div><div className="header-actions"><button className="secondary-button" disabled={saving} onClick={beginTotp}><Smartphone />{hasActiveTotp ? "Replace authenticator" : "Add authenticator"}</button><button className="primary-button" disabled={saving || typeof PublicKeyCredential === "undefined"} onClick={registerPasskey}><KeyRound />Register passkey</button></div></header><div className="method-grid">{personal.methods.map((item) => <div className="method-row" key={`${item.kind}-${item.id}`}><div className="method-icon">{item.kind === "passkey" ? <KeyRound /> : <Smartphone />}</div><div><strong>{item.label}</strong><span>{titleCase(item.kind)} · {item.active ? "Active" : "Pending setup"}</span></div><span className={`status-pill ${item.active ? "ready" : "attention"}`}>{item.active ? "Enabled" : "Pending"}</span></div>)}{!personal.methods.length && <div className="empty-inline">No MFA methods enrolled</div>}</div></section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">My sessions</h2><p className="panel-subtitle">Showing {personalRows.sessions.length} of {personal.totals.sessions} recent sessions</p></div>{personal.sessions.length > 5 && <button className="text-button" onClick={() => togglePersonal("sessions")}>{expandedPersonal.sessions ? "Show recent" : `View recent ${personal.sessions.length}`}</button>}</header><div className="table-wrap"><table><thead><tr><th>Device</th><th>IP address</th><th>Risk</th><th>Last seen</th><th>Status</th><th>Action</th></tr></thead><tbody>{personalRows.sessions.map((item) => <tr key={item.id}><td><div className="device-cell"><Laptop />{item.device}{item.id === personal.current_session_id && <small>Current</small>}</div></td><td>{item.ip_address || "—"}</td><td>{item.risk_score}</td><td>{formatDate(item.last_seen_at)}</td><td><span className={`status-pill ${item.status === "active" ? "active" : "attention"}`}>{titleCase(item.status)}</span></td><td>{item.status === "active" && item.id !== personal.current_session_id ? <button className="text-button danger-text" onClick={() => revokeSession(item.id)}>Revoke</button> : "—"}</td></tr>)}</tbody></table></div></section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">My devices</h2><p className="panel-subtitle">Showing {personalRows.devices.length} of {personal.totals.devices} browser identities for your account</p></div>{personal.devices.length > 5 && <button className="text-button" onClick={() => togglePersonal("devices")}>{expandedPersonal.devices ? "Show recent" : `View recent ${personal.devices.length}`}</button>}</header><div className="table-wrap"><table><thead><tr><th>Device</th><th>Platform</th><th>Last seen</th><th>Status</th><th>Action</th></tr></thead><tbody>{personalRows.devices.map((item) => <tr key={item.id}><td>{item.label}</td><td>{item.platform || "Web"}</td><td>{formatDate(item.last_seen_at)}</td><td>{item.revoked_at ? "Revoked" : "Registered"}</td><td>{!item.revoked_at && <button className="danger-text" disabled={saving} onClick={() => deviceAction(item.id)}>Revoke</button>}</td></tr>)}</tbody></table></div></section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Authentication history</h2><p className="panel-subtitle">Showing {personalRows.history.length} of {personal.totals.history} successful, challenged and failed events</p></div>{personal.history.length > 5 && <button className="text-button" onClick={() => togglePersonal("history")}>{expandedPersonal.history ? "Show recent" : `View recent ${personal.history.length}`}</button>}</header><div className="table-wrap"><table><thead><tr><th>Event</th><th>Outcome</th><th>IP address</th><th>Risk</th><th>Signals</th><th>Time</th></tr></thead><tbody>{personalRows.history.map((item) => <tr key={item.id}><td>{titleCase(item.event)}</td><td><span className={`status-pill ${item.outcome === "success" ? "active" : "attention"}`}>{titleCase(item.outcome)}</span></td><td>{item.ip_address || "—"}</td><td>{item.risk_score}</td><td>{item.risk_reasons.map(titleCase).join(", ") || "None"}</td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table></div></section></div>}

    {tab === "policy" && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Tenant security policy</h2><p className="panel-subtitle">Authentication, network and loss-prevention enforcement</p></div></header><form onSubmit={savePolicy}><div className="form-grid policy-grid">
      <label className="field"><span>Session timeout (minutes)</span><input name="session_timeout_minutes" type="number" min="5" max="720" defaultValue={catalog.policy.session_timeout_minutes} required /></label><label className="field"><span>Concurrent sessions</span><input name="maximum_concurrent_sessions" type="number" min="1" max="10" defaultValue={catalog.policy.maximum_concurrent_sessions} required /></label><label className="field"><span>Step-up validity (minutes)</span><input name="step_up_minutes" type="number" min="2" max="30" defaultValue={catalog.policy.step_up_minutes} required /></label><label className="field"><span>Failed login limit</span><input name="failed_login_limit" type="number" min="3" max="20" defaultValue={catalog.policy.failed_login_limit} required /></label><label className="field"><span>Lockout (minutes)</span><input name="lockout_minutes" type="number" min="1" max="1440" defaultValue={catalog.policy.lockout_minutes} required /></label><label className="field"><span>Alert risk threshold</span><input name="alert_risk_threshold" type="number" min="0" max="100" defaultValue={catalog.policy.alert_risk_threshold} required /></label><label className="field"><span>VPN risk threshold</span><input name="vpn_risk_threshold" type="number" min="0" max="100" defaultValue={catalog.policy.vpn_risk_threshold} required /></label><label className="field"><span>Approved CIDR networks</span><input name="approved_networks" defaultValue={catalog.policy.approved_networks.join(", ")} placeholder="10.0.0.0/8, 192.168.0.0/16" /></label><label className="field"><span>Allowed country codes</span><input name="allowed_countries" defaultValue={catalog.policy.allowed_countries.join(", ")} placeholder="IN, SG" /></label>
       <div className="policy-toggles"><label className="field check-field"><input name="require_mfa" type="checkbox" defaultChecked={catalog.policy.require_mfa} /><span>Require MFA</span></label><label className="field check-field"><input name="require_trusted_device" type="checkbox" defaultChecked={catalog.policy.require_trusted_device} /><span>Require trusted device</span></label><label className="field check-field"><input name="dlp_enabled" type="checkbox" defaultChecked={catalog.policy.dlp_enabled} /><span>Enable DLP controls</span></label></div>
     </div><footer className="inline-form-footer"><span>Policy version {catalog.policy.version}</span><button className="primary-button" disabled={saving}><ShieldCheck />Save policy</button></footer></form></section>}

    {tab === "evaluation_security" && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Evaluation security</h2><p className="panel-subtitle">These settings apply when an evaluator starts a protected evaluation session.</p></div></header><form onSubmit={savePolicy}><div className="form-grid policy-grid">
       <div className="policy-section-title">Session protection</div><p className="policy-section-help">Controls how long a protected evaluator session can run, how often it reports health, when missing face checks pause work, and how long evidence is retained.</p><label className="field"><span>Session timeout (minutes)</span><input name="evaluation_session_timeout_minutes" type="number" min="15" max="720" defaultValue={catalog.policy.evaluation_session_timeout_minutes} required /></label><label className="field"><span>Heartbeat (seconds)</span><input name="evaluation_heartbeat_seconds" type="number" min="5" max="60" defaultValue={catalog.policy.evaluation_heartbeat_seconds} required /></label><label className="field"><span>No-face threshold (seconds)</span><input name="evaluation_no_face_seconds" type="number" min="10" max="300" defaultValue={catalog.policy.evaluation_no_face_seconds} required /></label><label className="field"><span>Evidence retention (days)</span><input name="evaluation_retention_days" type="number" min="1" max="365" defaultValue={catalog.policy.evaluation_retention_days} required /></label>
       <div className="policy-section-title">ADMIEZO AI Assistant</div><p className="policy-section-help">AI mode is controlled by the Super Admin. The fallback threshold indicates when a human must review an AI-assisted result.</p><label className="field"><span>Evaluation mode</span><select name="ai_evaluation_mode" defaultValue={catalog.policy.ai_evaluation_mode} disabled><option value="disabled">No AI</option><option value="assistive">AI assisted evaluation</option><option value="autonomous">Autonomous AI evaluation</option></select><small>University API status: {catalog.ai_provider.available ? "API key verified" : catalog.ai_provider.message}</small></label><label className="field"><span>Human fallback threshold (%)</span><input name="ai_confidence_threshold" type="number" value={catalog.policy.ai_confidence_threshold} disabled readOnly /></label><input type="hidden" name="ai_evaluation_mode" value={catalog.policy.ai_evaluation_mode} /><input type="hidden" name="ai_confidence_threshold" value={catalog.policy.ai_confidence_threshold} /><input type="hidden" name="ai_model_name" value={catalog.policy.ai_model_name || "admiezo-ai-v1"} />
       <div className="policy-section-title">Evaluator controls</div><p className="policy-section-help">These switches are enforced when an evaluator starts the protected session. Strict mode applies the platform safeguards automatically.</p><div className="policy-toggles"><label className="field check-field"><input name="evaluation_strict_mode" type="checkbox" defaultChecked={catalog.policy.evaluation_strict_mode} /><span>Strict evaluation mode</span></label><label className="field check-field"><input name="evaluation_identity_verification_required" type="checkbox" defaultChecked={catalog.policy.evaluation_identity_verification_required} /><span>Require face verification</span></label><label className="field check-field"><input name="evaluation_camera_required" type="checkbox" defaultChecked={catalog.policy.evaluation_camera_required} /><span>Require webcam</span></label><label className="field check-field"><input name="evaluation_fullscreen_required" type="checkbox" defaultChecked={catalog.policy.evaluation_fullscreen_required} /><span>Require fullscreen</span></label><label className="field check-field"><input name="evaluation_single_screen_required" type="checkbox" defaultChecked={catalog.policy.evaluation_single_screen_required} /><span>Require one display</span></label><label className="field check-field"><input name="evaluation_mobile_allowed" type="checkbox" defaultChecked={catalog.policy.evaluation_mobile_allowed} /><span>Allow mobile devices</span></label><label className="field check-field"><input name="evaluation_event_recording" type="checkbox" defaultChecked={catalog.policy.evaluation_event_recording} /><span>Capture event clips</span></label><label className="field check-field"><input name="evaluation_pause_on_violation" type="checkbox" defaultChecked={catalog.policy.evaluation_pause_on_violation} /><span>Pause on security violation</span></label><label className="field check-field"><input name="evaluation_require_resume_step_up" type="checkbox" defaultChecked={catalog.policy.evaluation_require_resume_step_up} /><span>Require re-authentication to resume</span></label><label className="field check-field"><input name="evaluation_allow_clipboard" type="checkbox" defaultChecked={catalog.policy.evaluation_allow_clipboard} /><span>Allow copy and paste</span></label><label className="field check-field"><input name="evaluation_allow_download" type="checkbox" defaultChecked={catalog.policy.evaluation_allow_download} /><span>Allow downloads</span></label><label className="field check-field"><input name="evaluation_allow_print" type="checkbox" defaultChecked={catalog.policy.evaluation_allow_print} /><span>Allow printing</span></label></div>
     </div><footer className="inline-form-footer"><span>Policy version {catalog.policy.version}</span><button className="primary-button" disabled={saving}><ShieldCheck />Save evaluation policy</button></footer></form></section>}

    {tab === "access" && <div className="security-stack"><div className="workspace-toolbar"><div><strong>Privileged access</strong><p>Time-bound elevation with independent approval and full auditing.</p></div><div className="header-actions"><button className="secondary-button" onClick={() => setModal("emergency")}><ShieldAlert />Emergency grant</button><button className="primary-button" onClick={() => setModal("request")}><Plus />Request access</button></div></div><section className="panel"><div className="table-wrap"><table><thead><tr><th>Requester</th><th>Role</th><th>Reason</th><th>Expires</th><th>Status</th><th>Decision</th></tr></thead><tbody>{catalog.access_requests.map((item) => <tr key={item.id}><td>{item.requester}</td><td>{titleCase(item.requested_role)}</td><td>{item.reason}</td><td>{formatDate(item.expires_at)}</td><td><span className={`status-pill ${item.status}`}>{titleCase(item.status)}</span></td><td>{item.status === "pending" ? <div className="row-actions"><button onClick={() => decideAccess(item, true)}>Approve</button><button className="danger-text" onClick={() => decideAccess(item, false)}>Reject</button></div> : item.decided_by || "—"}</td></tr>)}</tbody></table></div>{!catalog.access_requests.length && <div className="empty-state">No privileged access requests.</div>}</section>{catalog.emergency_grants.length > 0 && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Emergency grants</h2><p className="panel-subtitle">Short-lived incident access with explicit revocation</p></div></header><div className="table-wrap"><table><thead><tr><th>User</th><th>Role</th><th>Incident</th><th>Expires</th><th>Status</th></tr></thead><tbody>{catalog.emergency_grants.map((grant) => <tr key={grant.id}><td>{grant.user}</td><td>{titleCase(grant.role)}</td><td>{grant.incident_reference}</td><td>{formatDate(grant.expires_at)}</td><td>{grant.revoked_at ? <span className="status-pill attention">Revoked</span> : <button className="text-button danger-text" onClick={() => revokeEmergency(grant)}>Revoke</button>}</td></tr>)}</tbody></table></div></section>}<section className="panel"><header className="panel-header"><div><h2 className="panel-title">User access</h2><p className="panel-subtitle">Create accounts and grant only the modules each person needs</p></div><button className="primary-button" onClick={() => { if (requireStepUp()) setModal("create-member"); }}><UserPlus />Add user</button></header><div className="table-wrap"><table><thead><tr><th>User</th><th>Institution</th><th>Role</th><th>Modules</th><th>Status</th><th>Action</th></tr></thead><tbody>{catalog.members.map((item) => <tr key={item.id}><td><strong>{item.name}</strong><br /><small>{item.email}</small></td><td>{item.institution}</td><td>{titleCase(item.role)}</td><td>{item.enabled_modules.length ? item.enabled_modules.map(titleCase).join(", ") : "University defaults"}</td><td><span className={`status-pill ${item.is_active ? "active" : "attention"}`}>{item.is_active ? "Active" : "Inactive"}</span></td><td><button className="text-button" onClick={() => { setSelectedMember(item); setEditMemberRole({ id: item.id, role: item.role }); setModal("member"); }}>Edit access</button></td></tr>)}</tbody></table></div></section></div>}

    {tab === "alerts" && <div className="security-stack"><section className="panel"><header className="panel-header"><div><h2 className="panel-title">Security alerts</h2><p className="panel-subtitle">Risk detections and investigation state</p></div></header><div className="table-wrap"><table><thead><tr><th>Severity</th><th>Category</th><th>Alert</th><th>Status</th><th>Created</th><th>Action</th></tr></thead><tbody>{catalog.alerts.map((item) => <tr key={item.id}><td><span className={`status-pill ${item.severity}`}>{titleCase(item.severity)}</span></td><td>{titleCase(item.category)}</td><td>{item.title}</td><td>{titleCase(item.status)}</td><td>{formatDate(item.created_at)}</td><td><div className="row-actions">{item.status === "open" && <button onClick={() => changeAlertStatus(item, "investigating")}>Investigate</button>}{item.status !== "resolved" && <button onClick={() => changeAlertStatus(item, "resolved")}>Resolve</button>}</div></td></tr>)}</tbody></table></div>{!catalog.alerts.length && <div className="empty-state"><div><ShieldCheck /><strong>No security alerts</strong><p>Risk detections will appear here for investigation.</p></div></div>}</section>{catalog.dlp_incidents.length > 0 && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Data loss prevention</h2><p className="panel-subtitle">Candidate identity blocked at evaluation-core boundaries</p></div></header><div className="table-wrap"><table><thead><tr><th>Classification</th><th>Rule</th><th>Channel</th><th>Resource</th><th>Status</th><th>Detected</th></tr></thead><tbody>{catalog.dlp_incidents.map((item) => <tr key={item.id}><td>{titleCase(item.data_classification)}</td><td>{titleCase(item.rule)}</td><td>{titleCase(item.channel)}</td><td>{item.resource_reference}</td><td><span className="status-pill attention">{titleCase(item.status)}</span></td><td>{formatDate(item.created_at)}</td></tr>)}</tbody></table></div></section>}</div>}

    {tab === "infrastructure" && <div className="security-stack"><div className="workspace-toolbar"><div><strong>Security integrations</strong><p>Deployment controls report actual configured state.</p></div><button className="primary-button" onClick={() => setModal("oidc")}><Plus />Add SSO provider</button></div><section className="control-matrix"><div className="control-item"><div className={clockDriftSeconds <= 30 ? "control-ok" : "control-pending"}>{clockDriftSeconds <= 30 ? <Check /> : <AlertTriangle />}</div><div><strong>Authenticator clock</strong><span>{clockDriftSeconds <= 30 ? `Synchronized · ${clockDriftSeconds}s drift` : `Clock drift is ${clockDriftSeconds}s · synchronize this device`}</span></div></div>{Object.entries(catalog.controls).map(([key, enabled]) => <div className="control-item" key={key}><div className={enabled ? "control-ok" : "control-pending"}>{enabled ? <Check /> : <AlertTriangle />}</div><div><strong>{titleCase(key)}</strong><span>{enabled ? "Configured" : "Awaiting deployment configuration"}</span></div></div>)}</section><section className="panel"><header className="panel-header"><div><h2 className="panel-title">Institutional SSO</h2><p className="panel-subtitle">OIDC providers with encrypted client secrets</p></div></header><div className="table-wrap"><table><thead><tr><th>Name</th><th>Issuer</th><th>Client ID</th><th>Domain</th><th>Status</th></tr></thead><tbody>{catalog.oidc_providers.map((item) => <tr key={item.id}><td>{item.name}</td><td>{item.issuer}</td><td>{item.client_id}</td><td>{item.domain_hint || "Any"}</td><td><span className="status-pill active">{item.is_active ? "Active" : "Inactive"}</span></td></tr>)}</tbody></table></div>{!catalog.oidc_providers.length && <div className="empty-state">No institutional SSO providers configured.</div>}</section></div>}

    {modal === "step-up" && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Re-authenticate</h2><p>Required before privileged security actions.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={stepUp}><div className="form-grid one-column"><label className="field"><span>Password</span><input name="password" type="password" autoComplete="current-password" /></label><div className="form-divider">or</div><label className="field"><span>Authenticator code</span><input name="code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} /></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}><LockKeyhole />Verify</button></footer></form></div></div>}
    {modal === "totp" && totp && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{hasActiveTotp ? "Replace authenticator" : "Set up authenticator"}</h2><p>Scan the QR code, then confirm a generated code. Your current method remains active until confirmation.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={confirmTotp}><div className="totp-setup"><QRCodeSVG value={totp.provisioning_uri} size={164} level="M" /><div><span>Manual setup key</span><code>{totp.secret}</code><label className="field"><span>Six-digit code</span><input name="code" inputMode="numeric" autoComplete="one-time-code" minLength={6} maxLength={6} required autoFocus /></label></div></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>{hasActiveTotp ? "Confirm replacement" : "Enable MFA"}</button></footer></form></div></div>}
    {modal === "request" && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Request privileged access</h2><p>An independent administrator must approve this request.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={requestAccess}><div className="form-grid one-column"><label className="field"><span>Access role</span><select name="requested_role" defaultValue="audit_export"><option value="audit_export">Audit export</option><option value="identity_resolution">Identity resolution</option><option value="security_administration">Security administration</option><option value="emergency_evaluation">Emergency evaluation</option></select></label><label className="field"><span>Duration (minutes)</span><input name="duration_minutes" type="number" min="15" max="480" defaultValue="60" required /></label><label className="field"><span>Business reason</span><textarea name="reason" rows={3} required /></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>Submit request</button></footer></form></div></div>}
    {modal === "emergency" && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Emergency access</h2><p>Issue short-lived incident access to another active member.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={grantEmergency}><div className="form-grid one-column"><label className="field"><span>Member</span><select name="user_id" defaultValue="" required><option value="" disabled>Select member</option>{catalog.members.filter((item) => item.is_active).map((item) => <option value={item.user_id} key={item.id}>{item.name} · {titleCase(item.role)}</option>)}</select></label><label className="field"><span>Emergency role</span><select name="role" defaultValue="exam_controller"><option value="university_admin">University administrator</option><option value="exam_controller">Examination controller</option><option value="receiving_officer">Receiving officer</option><option value="auditor">Auditor</option></select></label><label className="field"><span>Incident reference</span><input name="incident_reference" minLength={4} required /></label><label className="field"><span>Duration (minutes)</span><input name="duration_minutes" type="number" min="5" max="120" defaultValue="30" required /></label><label className="field"><span>Justification</span><textarea name="justification" rows={3} minLength={12} required /></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}><ShieldAlert />Grant access</button></footer></form></div></div>}
    {modal === "create-member" && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Add university user</h2><p>Create sign-in credentials and select the modules this person can access.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={createMember}><div className="form-grid"><label className="field"><span>First name</span><input name="first_name" required /></label><label className="field"><span>Last name</span><input name="last_name" required /></label><label className="field full-field"><span>Institutional email</span><input name="email" type="email" autoComplete="off" required /></label><label className="field"><span>Role</span><select name="role" value={newMemberRole} onChange={(event) => setNewMemberRole(event.target.value)}><option value="university_admin">University administrator</option><option value="exam_controller">Examination controller</option><option value="evaluator">Evaluator</option><option value="bundle_preparer">Bundle dispatch operator</option><option value="intake_receiver">Bundle and packet receiver</option><option value="scan_operator">Script scanning operator</option><option value="operations_supervisor">Operations supervisor</option><option value="script_receiver">Script receiver</option><option value="scanner_operator">Scanner operator</option><option value="custody_officer">Chain custody officer</option><option value="receiving_officer">Receiving officer</option><option value="auditor">Auditor</option></select></label><DynamicFields fields={customFields} /></div><ModuleSelector key={newMemberRole} modules={modulesForRole(newMemberRole, catalog.module_catalog)} selected={fixedRoleModules[newMemberRole] || []} locked={Boolean(fixedRoleModules[newMemberRole])} />{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}><UserPlus />Create user</button></footer></form></div></div>}
    {modal === "member" && selectedMember && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true">
      <header className="modal-header"><div><h2>Edit access</h2><p>{selectedMember.name} · {selectedMember.institution}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header>
      <div className="account-auth-status"><KeyRound /><div><strong>Authenticator app</strong><span>{selectedMember.authenticator_enabled ? "Enabled" : "Not enrolled"}</span></div></div>
      <form onSubmit={saveMember}><div className="form-grid"><label className="field"><span>Role</span><select name="role" value={editMemberRole.id === selectedMember.id ? editMemberRole.role : selectedMember.role} onChange={(event) => setEditMemberRole({ id: selectedMember.id, role: event.target.value })}><option value="platform_admin">Platform administrator</option><option value="university_admin">University administrator</option><option value="exam_controller">Examination controller</option><option value="evaluator">Evaluator</option><option value="bundle_preparer">Bundle dispatch operator</option><option value="intake_receiver">Bundle and packet receiver</option><option value="scan_operator">Script scanning operator</option><option value="operations_supervisor">Operations supervisor</option><option value="script_receiver">Script receiver</option><option value="scanner_operator">Scanner operator</option><option value="custody_officer">Chain custody officer</option><option value="receiving_officer">Receiving officer</option><option value="auditor">Auditor</option></select></label><label className="field"><span>Additional permissions</span><input name="permissions" defaultValue={selectedMember.permissions.join(", ")} placeholder="audit.export, identity.resolve" /></label><label className="field check-field"><input name="is_active" type="checkbox" defaultChecked={selectedMember.is_active} /><span>Active membership</span></label></div><MemberModuleSelector member={selectedMember} editRole={editMemberRole} modules={catalog.module_catalog} />{error && <div className="form-error">{error}</div>}
        <footer className="modal-footer">{selectedMember.can_reset_authenticator && <button type="button" className="secondary-button danger-text" onClick={() => { if (requireStepUp("reset-authenticator")) setModal("reset-authenticator"); }}><KeyRound />Reset authenticator</button>}<button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}><UserCog />Save access</button></footer>
      </form>
    </div></div>}
    {modal === "reset-authenticator" && selectedMember && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Reset authenticator</h2><p>{selectedMember.email}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><div className="form-grid one-column"><p>Existing authenticator codes will stop working and all active sessions will end. The user will scan a new QR code at the next password sign-in when MFA is required.</p></div>{error && <div className="form-error" role="alert">{error}</div>}<footer className="modal-footer"><button className="secondary-button" onClick={() => setModal("member")}>Back</button><button className="primary-button" disabled={saving} onClick={resetAuthenticator}><KeyRound />Reset authenticator</button></footer></div></div>}
    {modal === "oidc" && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>Add institutional SSO</h2><p>Client secrets are encrypted before storage.</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header><form onSubmit={createProvider}><div className="form-grid one-column"><label className="field"><span>Provider name</span><input name="name" required /></label><label className="field"><span>OIDC issuer</span><input name="issuer" type="url" placeholder="https://login.example.edu" required /></label><label className="field"><span>Client ID</span><input name="client_id" required /></label><label className="field"><span>Client secret</span><input name="client_secret" type="password" autoComplete="new-password" required /></label><label className="field"><span>Scopes</span><input name="scopes" defaultValue="openid email profile" required /></label><label className="field"><span>Institutional email domain</span><input name="domain_hint" placeholder="example.edu" /></label></div>{error && <div className="form-error">{error}</div>}<footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setModal(null)}>Cancel</button><button className="primary-button" disabled={saving}>Add provider</button></footer></form></div></div>}
    {credential && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>User login ready</h2><p>Credentials are shown only in this handover.</p></div><button className="icon-button" title="Close" onClick={() => setCredential(null)}><X /></button></header><div className="credential-sheet"><label><span>User ID</span><code>{credential.email}</code></label>{credential.temporary_password ? <label><span>Temporary password</span><code>{credential.temporary_password}</code><button className="icon-button" title="Copy credentials" onClick={() => navigator.clipboard.writeText(`User ID: ${credential.email}\nTemporary password: ${credential.temporary_password}`)}><Copy /></button></label> : <p>This person already had an ADMIEZO identity. Their current password remains unchanged.</p>}</div><footer className="modal-footer"><button className="primary-button" onClick={() => setCredential(null)}>Done</button></footer></div></div>}
  </div>;
}

function ModuleSelector({ modules, selected = [], locked = false }: { modules: string[]; selected?: string[]; locked?: boolean }) {
  return <fieldset className="module-entitlement-grid"><legend>Module access{locked ? " · fixed by role" : ""}</legend>{modules.map((module) => <label key={module}>{locked && <input type="hidden" name="modules" value={module} />}<input type="checkbox" name={locked ? undefined : "modules"} value={module} defaultChecked={selected.includes(module)} disabled={locked} /><span>{moduleLabel[module] || titleCase(module)}</span></label>)}</fieldset>;
}

function MemberModuleSelector({ member, editRole, modules }: { member: Member; editRole: { id: string; role: string }; modules: string[] }) {
  const role = editRole.id === member.id ? editRole.role : member.role;
  return <ModuleSelector key={role} modules={modulesForRole(role, modules)} selected={fixedRoleModules[role] || member.enabled_modules} locked={Boolean(fixedRoleModules[role])} />;
}
