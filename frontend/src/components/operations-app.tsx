"use client";

import {
  AlertTriangle, Archive, ArrowUpRight, BookOpenCheck, Boxes, Check,
  ChevronRight, ClipboardCheck, FileCheck2, Files, Fingerprint, Gauge, History,
  EyeOff, KeyRound, LayoutDashboard, LogOut, Menu, Network, RefreshCw, ShieldCheck, SlidersHorizontal,
  UserRoundCheck, Users, X, ScanLine, ScrollText, GitCompareArrows, Workflow, ServerCog,
} from "lucide-react";
import { FormEvent, MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { QRCodeSVG } from "qrcode.react";
import { ConfigurationWorkspace } from "@/components/configuration-workspace";
import { EnterpriseWorkspace } from "@/components/enterprise-workspace";
import { EvaluatorWorkspace } from "@/components/evaluator-workspace";
import { SecurityWorkspace } from "@/components/security-workspace";
import { GuidedIntakeWorkspace } from "@/components/guided-intake-workspace";
import { IntakeOperationsDashboard } from "@/components/intake-operations-dashboard";
import { RepositoryWorkspace } from "@/components/repository-workspace";
import { GuidedMaskingWorkspace } from "@/components/guided-masking-workspace";
import { AllocationWorkspace } from "@/components/allocation-workspace";
import { EvaluationWorkspace } from "@/components/evaluation-workspace";
import { RubricWorkspace } from "@/components/rubric-workspace";
import { GovernanceWorkspace } from "@/components/governance-workspace";
import { ValuationWorkspace } from "@/components/valuation-workspace";
import { AdvancedOperationsWorkspace } from "@/components/advanced-operations-workspace";
import { PlatformAdminWorkspace } from "@/components/platform-admin-workspace";
import { PlatformAuditWorkspace } from "@/components/platform-audit-workspace";
import { NotificationCenter } from "@/components/notification-center";
import { AccountSettings } from "@/components/account-settings";
import { LanguageSelector } from "@/components/language-selector";
import { FullPageLocalization } from "@/components/full-page-localization";
import { deviceContext, getPasskey } from "@/lib/webauthn";
import { csrfFetch, SESSION_EXPIRED_EVENT } from "@/lib/api";

type UserContext = {
  user: { id: number; name: string; email: string };
  tenant: { id: string; name: string; code: string };
  role: string;
  permissions: string[];
  must_change_password: boolean;
  enabled_modules: string[];
  tenants: { id: string; name: string; role: string }[];
  session: { id: string; timeout_minutes: number };
};

type Overview = {
  generated_at: string;
  session: { name: string; term: string; status: string } | null;
  metrics: Record<string, number>;
  pipeline: { key: string; label: string; count: number; state: string }[];
  attention: { label: string; count: number; severity: string }[];
  module_counts: Record<string, number>;
  papers: { id: string; code: string; title: string; programme: string; scripts: number; assigned: number; progress: number; status: string; readiness: string }[];
  recent_events: { id: string; script: string; transition: string; location: string; at: string }[];
};

type GenericRow = Record<string, unknown>;
type SsoProvider = { id: string; name: string; domain_hint: string };
type DomainContext = { scope: "platform" | "university"; hostname: string; university: { name: string; code: string; status: string } | null };
type MfaEnrollment = { method_id: string; secret: string; provisioning_uri: string };
type ViewKey = "platformAdmin" | "dashboard" | "configuration" | "evaluators" | "receiving" | "custody" | "digitization" | "anonymisation" | "repository" | "allocation" | "rubrics" | "assignmentGovernance" | "evaluation" | "valuation" | "assessmentControl" | "liveControl" | "serviceControl" | "platformControl" | "security" | "tenancy" | "audit";

const navGroups: { label: string; items: { key: ViewKey; label: string; icon: typeof Gauge }[] }[] = [
  { label: "Operations", items: [
    { key: "dashboard", label: "Command centre", icon: LayoutDashboard },
    { key: "receiving", label: "Script receiving", icon: Boxes },
    { key: "custody", label: "Chain of custody", icon: Fingerprint },
    { key: "digitization", label: "Digitization", icon: ScanLine },
    { key: "liveControl", label: "Live operations", icon: Gauge },
  ]},
  { label: "Administration", items: [
    { key: "configuration", label: "Exam configuration", icon: SlidersHorizontal },
    { key: "evaluators", label: "Evaluator master", icon: Users },
    { key: "allocation", label: "Allocation engine", icon: Network },
    { key: "assignmentGovernance", label: "Allocation history", icon: Workflow },
    { key: "serviceControl", label: "Results & services", icon: FileCheck2 },
  ]},
  { label: "Evaluation", items: [
    { key: "anonymisation", label: "Anonymization", icon: EyeOff },
    { key: "repository", label: "Script repository", icon: Archive },
    { key: "rubrics", label: "Marking schemes", icon: ScrollText },
    { key: "evaluation", label: "Evaluation desk", icon: BookOpenCheck },
    { key: "valuation", label: "Valuation review", icon: GitCompareArrows },
    { key: "assessmentControl", label: "Assessment control", icon: ClipboardCheck },
  ]},
  { label: "Governance", items: [
    { key: "security", label: "Access governance", icon: ShieldCheck },
    { key: "tenancy", label: "Enterprise settings", icon: Gauge },
    { key: "audit", label: "Audit trail", icon: History },
    { key: "platformControl", label: "Platform operations", icon: Network },
  ]},
];

const evaluatorViews = new Set<ViewKey>(["evaluation"]);
const intakeDeskViews: Record<string, ViewKey> = { bundle_preparer: "receiving", intake_receiver: "custody", scan_operator: "digitization" };
const supervisorViews = new Set<ViewKey>(["dashboard", "receiving", "custody", "digitization"]);
const guidedRefreshViews = new Set<ViewKey>(["receiving", "custody", "digitization", "anonymisation", "allocation", "assignmentGovernance"]);
const platformViews = new Set<ViewKey>(["platformAdmin", "platformControl", "audit"]);
const platformHashToView: Record<string, ViewKey> = { universities: "platformAdmin", operations: "platformControl", audit: "audit" };
const platformViewToHash: Partial<Record<ViewKey, string>> = { platformAdmin: "universities", platformControl: "operations", audit: "audit" };
const platformStorageKey = "admiezo-platform-view";
const platformNavigation = [{ label: "Platform", items: [
  { key: "platformAdmin" as ViewKey, label: "Universities", icon: Network },
  { key: "platformControl" as ViewKey, label: "Platform operations", icon: ServerCog },
  { key: "audit" as ViewKey, label: "Platform audit", icon: History },
] }];
const tenantNavigation = navGroups.map((group) => ({ ...group, items: group.items.filter((item) => item.key !== "platformControl") })).filter((group) => group.items.length);
const viewModule: Partial<Record<ViewKey, string>> = {
  configuration: "configuration", evaluators: "evaluators", receiving: "receiving", custody: "custody", digitization: "digitization",
  anonymisation: "anonymisation", repository: "repository", allocation: "allocation", assignmentGovernance: "assignment_governance",
  rubrics: "rubrics", evaluation: "evaluation", valuation: "valuation", assessmentControl: "assessment", liveControl: "operations",
  serviceControl: "services", security: "security", audit: "audit", tenancy: "enterprise",
};
const operationalRoleHome: Record<string, ViewKey> = {
  receiving_officer: "receiving",
  script_receiver: "receiving",
  scanner_operator: "digitization",
  custody_officer: "custody",
  auditor: "audit",
};

function navigationFor(role: string, platformMode: boolean, enabledModules: string[]) {
  const allowed = (item: { key: ViewKey }) => !viewModule[item.key] || enabledModules.includes(viewModule[item.key]!);
  const entitledTenantNavigation = tenantNavigation.map((group) => ({ ...group, items: group.items.filter(allowed) })).filter((group) => group.items.length);
  if (role === "platform_admin") return platformMode ? platformNavigation : [{ label: "Platform", items: [{ key: "platformAdmin" as ViewKey, label: "Back to control plane", icon: Network }] }, ...entitledTenantNavigation];
<<<<<<< HEAD
  const roleHome = operationalRoleHome[role];
  if (roleHome) return entitledTenantNavigation.map((group) => ({ ...group, items: group.items.filter((item) => item.key === roleHome) })).filter((group) => group.items.length);
=======
  if (intakeDeskViews[role]) return entitledTenantNavigation.map((group) => ({ ...group, items: group.items.filter((item) => item.key === intakeDeskViews[role]) })).filter((group) => group.items.length);
  if (role === "operations_supervisor") return entitledTenantNavigation.map((group) => ({ ...group, items: group.items.filter((item) => supervisorViews.has(item.key)) })).filter((group) => group.items.length);
>>>>>>> main
  if (role !== "evaluator") return entitledTenantNavigation;
  return tenantNavigation
    .map((group) => ({ ...group, items: group.items.filter((item) => evaluatorViews.has(item.key) && allowed(item)) }))
    .filter((group) => group.items.length > 0);
}

function emptyOverview(): Overview {
  return { generated_at: new Date().toISOString(), session: null, metrics: {}, pipeline: [], attention: [], module_counts: {}, papers: [], recent_events: [] };
}

const viewMeta: Record<ViewKey, { eyebrow: string; title: string; description: string; endpoint?: string; countKey?: string; controls: string[] }> = {
  platformAdmin: { eyebrow: "Super Admin", title: "University management", description: "Provision and govern every isolated ADMIEZO university tenant.", controls: [] },
  dashboard: { eyebrow: "Live operations", title: "Evaluation operations", description: "Script movement, allocation and evaluation readiness.", controls: [] },
  configuration: { eyebrow: "Module 01", title: "Exam configuration", description: "Versioned paper rules, marks and readiness state.", endpoint: "/api/v1/config/papers", countKey: "configuration", controls: ["Optimistic locking", "Configuration freeze", "Readiness validation"] },
  evaluators: { eyebrow: "Module 02", title: "Evaluator master", description: "Verified profiles, capacity and lifecycle controls.", endpoint: "/api/v1/evaluators", countKey: "evaluators", controls: ["Unique evaluator IDs", "Subject expertise", "Status history"] },
  receiving: { eyebrow: "Module 08", title: "Script receiving", description: "Prepare bundle and packet manifests for dispatch or on-site scanning.", endpoint: "/api/v1/receiving/dispatches", countKey: "receiving", controls: ["Count reconciliation", "Exception routing", "Receiver confirmation"] },
  custody: { eyebrow: "Module 09", title: "Chain of custody", description: "Receive bundles, then scan and reconcile their packets.", endpoint: "/api/v1/custody/scripts", countKey: "custody", controls: ["Collision-safe barcode", "State machine", "Append-only events"] },
  digitization: { eyebrow: "Modules 10, 11 & 41", title: "Digitization control", description: "Open a received packet and upload each answer script.", controls: ["Distributed scan queue", "Deterministic enhancement", "Scheduled tamper detection"] },
  repository: { eyebrow: "Module 13", title: "Script repository", description: "Immutable asset metadata, integrity and retention.", endpoint: "/api/v1/repository/assets", countKey: "repository", controls: ["SHA-256 integrity", "Legal hold", "Signed media URLs"] },
  anonymisation: { eyebrow: "Module 12", title: "Candidate anonymization", description: "PII isolation, irreversible masking and dual-authorized resolution.", controls: ["Separate identity boundary", "Independent mask verification", "Two-person identity resolution"] },
  allocation: { eyebrow: "Module 06", title: "Allocation engine", description: "Blind assignments constrained by capacity and valuation round.", endpoint: "/api/v1/allocation/assignments", countKey: "allocation", controls: ["Blind allocation", "Capacity hard stop", "Conflict prevention"] },
  rubrics: { eyebrow: "Module 17", title: "Marking schemes", description: "Versioned criteria, guidance, approvals, freeze and clarifications.", controls: ["Two-person approval", "Content hashing", "Mandatory acknowledgement"] },
  assignmentGovernance: { eyebrow: "", title: "Allocation history", description: "Read-only record of allocation simulations and evaluator matches.", controls: [] },
  evaluation: { eyebrow: "Modules 14, 15 & 16", title: "Evaluation desk", description: "Secure script review, digital annotations and question-wise marking.", endpoint: "/api/v1/allocation/assignments", countKey: "allocation", controls: ["Five-minute media URLs", "Append-only mark revisions", "Previous valuations hidden"] },
  valuation: { eyebrow: "Modules 23 & 25", title: "Valuation review", description: "Independent round comparison, discrepancy reconciliation and final mark control.", controls: ["Blind valuations", "Threshold-based routing", "Immutable final mark lock"] },
  assessmentControl: { eyebrow: "Modules 24, 27 & 28", title: "Assessment control", description: "Moderation, revaluation and signed completion workflows.", controls: ["Independent decisions", "Final-mark checks", "Controlled release"] },
  liveControl: { eyebrow: "Modules 29 & 31–37", title: "Live operations", description: "Secure remote evaluation, monitoring, recovery and centre operations.", controls: ["Session evidence", "SLA workflows", "Continuity queue"] },
  serviceControl: { eyebrow: "Modules 38 & 39", title: "Results & student services", description: "Remuneration and protected student script services.", controls: ["Verified work units", "Payment reconciliation", "Expiring access"] },
  platformControl: { eyebrow: "Modules 42, 46, 48 & 49", title: "Platform operations", description: "Forensics, integrations, interface languages and recovery drills.", controls: ["Sealed evidence", "Idempotent handover", "Recovery objectives"] },
  security: { eyebrow: "Modules 04 & 40", title: "Access governance", description: "Authentication sessions, least privilege and protected operations.", controls: ["Session fixation protection", "Account lockout", "Privileged step-up policy"] },
  tenancy: { eyebrow: "Module 47", title: "Enterprise settings", description: "University hierarchy, institution policies and isolated records.", controls: ["Tenant-scoped queries", "Institution policies", "Role boundaries"] },
  audit: { eyebrow: "Platform control", title: "Audit trail", description: "Operational evidence emitted beside every domain write.", endpoint: "/api/v1/audit/events", controls: ["Atomic audit write", "Transactional outbox", "Tenant isolation"] },
};

const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
function formatValue(value: unknown) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}T/.test(text)) return new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(text));
  return titleCase(text);
}

function Login({ onSuccess, notice }: { onSuccess: () => void; notice?: string }) {
  const [email, setEmail] = useState("admin@admiezo.local");
  const [password, setPassword] = useState("ChangeMe123!");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mfaRequired, setMfaRequired] = useState(false);
  const [mfaEnrollment, setMfaEnrollment] = useState<MfaEnrollment | null>(null);
  const [code, setCode] = useState("");
  const [ssoProviders, setSsoProviders] = useState<SsoProvider[]>([]);
  const [domain, setDomain] = useState<DomainContext | null>(null);
  const loginEdited = useRef(false);
  useEffect(() => {
    csrfFetch("/api/v1/enterprise/domain-context").then((response) => response.ok ? response.json() : null).then((value: DomainContext | null) => {
      setDomain(value);
      if (value?.scope === "university" && !loginEdited.current) { setEmail(""); setPassword(""); }
    }).catch(() => setDomain(null));
    csrfFetch("/api/v1/auth/sso/providers")
      .then((response) => response.ok ? response.json() : [])
      .then((providers) => setSsoProviders(Array.isArray(providers) ? providers : []))
      .catch(() => setSsoProviders([]));
    const ssoResult = new URLSearchParams(window.location.search).get("sso");
    if (ssoResult === "failed" || ssoResult === "authenticator_required") {
      window.queueMicrotask(() => setError(ssoResult === "authenticator_required" ? "This university requires password and authenticator app sign-in." : "Institutional sign-in could not be completed. Check the account and try again."));
    }
    if (ssoResult) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const response = await csrfFetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password, ...deviceContext() }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Unable to sign in");
      if (response.status === 202) {
        if (body.challenge === "mfa_enrollment" && body.enrollment) setMfaEnrollment(body.enrollment);
        else setMfaRequired(true);
        return;
      }
      onSuccess();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to sign in"); }
    finally { setBusy(false); }
  }
  async function enrollMfa(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const response = await csrfFetch("/api/v1/auth/mfa/enroll", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ method_id: mfaEnrollment?.method_id, code }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Unable to enable the authenticator");
      onSuccess();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to enable the authenticator"); }
    finally { setBusy(false); }
  }
  async function verifyMfa(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const response = await csrfFetch("/api/v1/auth/mfa/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || "Unable to verify code"); }
      onSuccess();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to verify code"); }
    finally { setBusy(false); }
  }
  async function signInWithPasskey() {
    setBusy(true); setError("");
    try {
      const optionsResponse = await csrfFetch("/api/v1/auth/passkeys/login/options", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, ...deviceContext() }) });
      const options = await optionsResponse.json();
      if (!optionsResponse.ok) throw new Error(options.detail || "No passkey is available for this account");
      const credential = await getPasskey(options);
      const response = await csrfFetch("/api/v1/auth/passkeys/login/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ credential }) });
      if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.detail || "Passkey sign-in failed"); }
      onSuccess();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Passkey sign-in failed"); }
    finally { setBusy(false); }
  }
  async function signInWithSso(provider: SsoProvider) {
    setBusy(true); setError("");
    try {
      const response = await csrfFetch("/api/v1/auth/sso/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider_id: provider.id, ...deviceContext() }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "Institutional sign-in is unavailable");
      window.location.assign(body.authorization_url);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Institutional sign-in is unavailable"); setBusy(false); }
  }
  return <main className="login-shell">
    <section className="login-brand">
      <div className="login-logo"><span className="brand-mark">A</span> ADMIEZO</div>
      <div className="login-statement"><h1>{domain?.university?.name || "Evaluation integrity, from receipt to result."}</h1><p>{domain?.university ? "Dedicated, isolated university evaluation workspace" : "Multi-university evaluation control plane"}</p></div>
      <div className="login-foot">{domain?.hostname || "Secure institutional access"} · Session monitoring enabled</div>
    </section>
    <section className="login-form-wrap"><form className="login-form" onSubmit={mfaEnrollment ? enrollMfa : mfaRequired ? verifyMfa : submit}>
      <h2>{mfaEnrollment ? "Set up authenticator" : mfaRequired ? "Verify it’s you" : "Welcome back"}</h2><p>{mfaEnrollment ? "Scan this QR code with your authenticator app, then enter its six-digit code." : mfaRequired ? "Enter the six-digit code from your authenticator." : "Sign in to the evaluation control room."}</p>
      {!mfaRequired && !mfaEnrollment && <><label className="field"><span>Email address</span><input type="email" autoComplete="username" value={email} onChange={(event) => { loginEdited.current = true; setEmail(event.target.value); }} required /></label>
        <label className="field"><span>Password</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => { loginEdited.current = true; setPassword(event.target.value); }} required /></label></>}
      {mfaRequired && <label className="field"><span>Authenticator code</span><input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} minLength={6} maxLength={6} required autoFocus /></label>}
      {mfaEnrollment && <div className="totp-setup login-totp-setup"><QRCodeSVG value={mfaEnrollment.provisioning_uri} size={164} level="M" /><div><span>Manual setup key</span><code>{mfaEnrollment.secret}</code><label className="field"><span>Six-digit code</span><input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} minLength={6} maxLength={6} required autoFocus /></label></div></div>}
      {(error || notice) && <div className="form-error" role="alert">{error || notice}</div>}
      <button className="primary-button login-submit" disabled={busy}>{busy ? "Please wait…" : mfaEnrollment ? "Enable and continue" : mfaRequired ? "Verify and continue" : "Sign in"}<ArrowUpRight /></button>
      {!mfaRequired && !mfaEnrollment && <button className="secondary-button login-passkey" type="button" disabled={busy || !email} onClick={signInWithPasskey}><KeyRound />Use a passkey</button>}
      {!mfaRequired && !mfaEnrollment && ssoProviders.length > 0 && <><div className="form-divider">Institutional SSO</div>{ssoProviders.map((provider) => <button className="secondary-button login-passkey" type="button" disabled={busy} onClick={() => signInWithSso(provider)} key={provider.id}><ShieldCheck />Continue with {provider.name}</button>)}</>}
      {(mfaRequired || mfaEnrollment) && <button className="text-button login-back" type="button" onClick={() => { setMfaRequired(false); setMfaEnrollment(null); setCode(""); setError(""); }}>Use a different account</button>}
    </form></section>
  </main>;
}

function Dashboard({ overview, navigate }: { overview: Overview; navigate: (view: ViewKey) => void }) {
  const { t, i18n } = useTranslation();
  const locale = ({ en: "en-IN", hi: "hi-IN", kn: "kn-IN", ta: "ta-IN", te: "te-IN", mr: "mr-IN" } as Record<string, string>)[i18n.resolvedLanguage || "en"] || "en-IN";
  const receivedPercent = overview.metrics.expected_scripts ? Math.round(overview.metrics.received_scripts * 100 / overview.metrics.expected_scripts) : 0;
  const metrics = [
    { key: "expected", label: t("dashboard.metrics.expected"), value: overview.metrics.expected_scripts, detail: t("dashboard.metrics.expectedDetail"), icon: Files },
    { key: "received", label: t("dashboard.metrics.received"), value: overview.metrics.received_scripts, detail: t("dashboard.metrics.receivedDetail", { percent: receivedPercent }), icon: ClipboardCheck },
    { key: "assigned", label: t("dashboard.metrics.assigned"), value: overview.metrics.assigned_scripts, detail: t("dashboard.metrics.assignedDetail", { count: overview.metrics.registered_scripts }), icon: UserRoundCheck },
    { key: "progress", label: t("dashboard.metrics.progress"), value: `${overview.metrics.evaluation_progress}%`, detail: t("dashboard.metrics.progressDetail"), icon: FileCheck2 },
  ];
  return <>
    <div className="metric-band">{metrics.map((metric) => <div className="metric" key={metric.key}><div className="metric-label"><span>{metric.label}</span><metric.icon /></div><div className="metric-value">{typeof metric.value === "number" ? metric.value.toLocaleString(locale) : metric.value}</div><div className="metric-detail">{metric.detail}</div></div>)}</div>
    <div className="dashboard-grid">
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{t("dashboard.pipeline.title")}</h2><p className="panel-subtitle">{t("dashboard.pipeline.subtitle")}</p></div><button className="text-button" onClick={() => navigate("custody")}>{t("dashboard.pipeline.open")}</button></header><div className="pipeline">{overview.pipeline.map((stage) => <div className={`stage ${stage.state}`} key={stage.key}><div className="stage-node" /><div className="stage-label">{stage.label}</div><div className="stage-count">{stage.count.toLocaleString(locale)}</div></div>)}</div></section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{t("dashboard.attention.title")}</h2><p className="panel-subtitle">{t("dashboard.attention.subtitle")}</p></div></header><div className="attention-list">{overview.attention.map((item) => <div className="attention-row" key={item.label}><span className={`severity ${item.severity}`} /><div className="attention-copy"><div className="attention-label">{item.label}</div></div>{item.count ? <div className="attention-count">{item.count}</div> : <div className="attention-clear">{t("dashboard.attention.clear")}</div>}</div>)}</div></section>
    </div>
    <div className="lower-grid">
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{t("dashboard.papers.title")}</h2><p className="panel-subtitle">{t("dashboard.papers.subtitle")}</p></div><button className="text-button" onClick={() => navigate("configuration")}>{t("dashboard.papers.viewAll")}</button></header><div className="table-wrap"><table><thead><tr><th>{t("dashboard.papers.paper")}</th><th>{t("dashboard.papers.programme")}</th><th>{t("dashboard.papers.scripts")}</th><th>{t("dashboard.papers.allocation")}</th><th>{t("dashboard.papers.config")}</th></tr></thead><tbody>{overview.papers.map((paper) => <tr key={paper.id}><td className="paper-cell"><div className="paper-code">{paper.code}</div><div className="paper-title">{paper.title}</div></td><td>{paper.programme}</td><td>{paper.scripts}</td><td><div className="progress-cell"><div className="progress-track"><div className="progress-fill" style={{ width: `${paper.progress}%` }} /></div><span>{paper.progress}%</span></div></td><td><span className={`status-pill ${paper.readiness}`}>{paper.readiness}</span></td></tr>)}</tbody></table></div></section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{t("dashboard.custody.title")}</h2><p className="panel-subtitle">{t("dashboard.custody.subtitle")}</p></div></header><div className="event-list">{overview.recent_events.map((event) => <div className="event" key={event.id}><div className="event-icon"><ChevronRight /></div><div><div className="event-title">{event.script} · {event.transition}</div><div className="event-meta">{event.location} · {new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit" }).format(new Date(event.at))}</div></div></div>)}</div></section>
    </div>
    <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{t("dashboard.modules.title")}</h2><p className="panel-subtitle">{t("dashboard.modules.subtitle")}</p></div></header><div className="module-status-grid">{Object.entries(overview.module_counts).map(([module, count]) => <button key={module} onClick={() => navigate(module === "configuration" ? "configuration" : module === "evaluators" ? "evaluators" : module === "receiving" ? "receiving" : module === "custody" ? "custody" : module === "repository" ? "repository" : "allocation")}><span>{titleCase(module)}</span><strong>{count.toLocaleString(locale)}</strong><small>{count ? t("dashboard.modules.records") : t("dashboard.modules.ready")}</small></button>)}</div></section>
  </>;
}

function ModuleWorkspace({ view, overview }: { view: ViewKey; overview: Overview }) {
  const meta = viewMeta[view];
  const [rows, setRows] = useState<GenericRow[]>([]);
  const [loading, setLoading] = useState(Boolean(meta.endpoint));
  const [error, setError] = useState("");
  useEffect(() => {
    if (!meta.endpoint) return;
    const controller = new AbortController();
    csrfFetch(meta.endpoint, { signal: controller.signal }).then(async (response) => { if (!response.ok) throw new Error("Could not load this workspace"); return response.json(); }).then((data) => { setRows(Array.isArray(data) ? data : []); setError(""); }).catch((reason) => { if (reason.name !== "AbortError") setError(reason.message); }).finally(() => setLoading(false));
    return () => controller.abort();
  }, [meta.endpoint]);
  const columns = useMemo(() => {
    if (!rows.length) return [];
    if (view === "audit") return ["user_name", "action_performed", "date", "time", "ip_address", "script_id", "details"];
    const hidden = new Set(["id", "paper_id", "script_id", "tenant_id", "payload"]);
    return Object.keys(rows[0]).filter((key) => !hidden.has(key)).slice(0, 7);
  }, [rows, view]);
  const count = meta.countKey ? overview.module_counts[meta.countKey] || 0 : rows.length;
  return <div className={`workspace-grid ${view === "audit" ? "audit-workspace" : ""}`}>
    <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Current records</h2><p className="panel-subtitle">Tenant-scoped operational data</p></div>{view === "audit" && <span className="status-pill active">{count.toLocaleString("en-IN")} {count === 1 ? "record" : "records"}</span>}</header>
      {loading ? <div className="empty-state"><div className="spinner" /></div> : error ? <div className="empty-state"><div><AlertTriangle /><strong>{error}</strong></div></div> : rows.length ? <div className="table-wrap"><table><thead><tr>{columns.map((column) => <th key={column}>{titleCase(column)}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id || index)}>{columns.map((column) => <td key={column}>{column === "status" || column === "state" ? <span className={`status-pill ${String(row[column])}`}>{formatValue(row[column])}</span> : formatValue(row[column])}</td>)}</tr>)}</tbody></table></div> : <div className="empty-state"><div><Files /><strong>No records yet</strong><p>This workspace is ready for its first operational record.</p></div></div>}
    </section>
    {view !== "audit" && <aside className="panel module-summary"><div className="summary-count">{count.toLocaleString("en-IN")}</div><div className="summary-label">Records in this university</div><ul className="control-list">{meta.controls.map((control) => <li key={control}><Check />{control}</li>)}</ul></aside>}
  </div>;
}

function PasswordSetup({ onComplete, onSignOut }: { onComplete: () => Promise<void>; onSignOut: () => Promise<void> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); const data = new FormData(event.currentTarget);
    try {
      if (data.get("password") !== data.get("confirm")) throw new Error("Passwords do not match");
      const response = await csrfFetch("/api/v1/auth/password/complete-setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ new_password: data.get("password") }) });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Password setup failed");
      await onComplete();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Password setup failed");
    } finally {
      setBusy(false);
    }
  }
  return <div className="login-shell"><section className="login-brand"><div className="login-logo"><span className="brand-mark">A</span>ADMIEZO</div><div className="login-statement"><h1>Secure your university account.</h1><p>Replace the one-time credential before entering the isolated workspace.</p></div><div className="login-foot">Password setup is recorded in the tenant audit trail.</div></section><div className="login-form-wrap"><form className="login-form" onSubmit={submit}><h2>Set your password</h2><p>Use at least 12 characters and avoid common passwords.</p><label className="field"><span>New password</span><input name="password" type="password" minLength={12} autoComplete="new-password" required /></label><label className="field"><span>Confirm password</span><input name="confirm" type="password" minLength={12} autoComplete="new-password" required /></label>{error && <div className="form-error" role="alert">{error}</div>}<button className="primary-button login-submit" disabled={busy}><KeyRound />{busy ? "Updating..." : "Set password and continue"}</button><button type="button" className="text-button" onClick={onSignOut}>Sign out</button></form></div></div>;
}

export function OperationsApp() {
  const { t } = useTranslation();
  const [context, setContext] = useState<UserContext | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState<ViewKey>("dashboard");
  const [menuOpen, setMenuOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [globalError, setGlobalError] = useState("");
  const [loginNotice, setLoginNotice] = useState("");
  const [platformMode, setPlatformMode] = useState(true);
  const [auditRefreshToken, setAuditRefreshToken] = useState(0);
  const [workspaceRefreshToken, setWorkspaceRefreshToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [toast, setToast] = useState("");
  const endingSession = useRef(false);
  const endSession = useCallback((notice = "") => {
    endingSession.current = true;
    for (const key of Object.keys(sessionStorage)) if (key.startsWith("admiezo-last-activity-")) sessionStorage.removeItem(key);
    sessionStorage.removeItem("admiezo-secure-evaluation-id");
    setAccountOpen(false); setContext(null); setOverview(null); setChecking(false);
    setView("dashboard"); setPlatformMode(true); setGlobalError(""); setLoginNotice(notice);
  }, []);
  const load = useCallback(async () => {
    try {
      const meResponse = await csrfFetch("/api/v1/auth/me");
      if (meResponse.status === 401) { endSession(); return false; }
      if (!meResponse.ok) throw new Error("The identity service is unavailable");
      const current = await meResponse.json() as UserContext;
      if (endingSession.current) return false;
      if (current.must_change_password) {
        setContext(current); setOverview(emptyOverview()); setGlobalError("");
        return true;
      }
      if (current.role === "platform_admin" && platformMode) {
        setContext(current); setOverview(emptyOverview()); setView((currentView) => platformViews.has(currentView) ? currentView : "platformAdmin"); setGlobalError("");
        return true;
      }
      if (current.role === "evaluator") {
        setContext(current); setOverview(emptyOverview()); setView("evaluation"); setGlobalError("");
        return true;
      }
      if (intakeDeskViews[current.role]) {
        setContext(current); setOverview(emptyOverview()); setView(intakeDeskViews[current.role]); setGlobalError("");
        return true;
      }
      if (current.role === "operations_supervisor") {
        setContext(current); setOverview(emptyOverview()); setView((currentView) => supervisorViews.has(currentView) ? currentView : "dashboard"); setGlobalError("");
        return true;
      }
      if (operationalRoleHome[current.role]) {
        setContext(current); setOverview(emptyOverview()); setView(operationalRoleHome[current.role]); setGlobalError("");
        return;
      }
      const overviewResponse = await csrfFetch("/api/v1/operations/overview");
      if (!overviewResponse.ok) throw new Error("The operations API is unavailable");
      if (endingSession.current) return false;
      setContext(current); setOverview(await overviewResponse.json()); setGlobalError("");
      return true;
    } catch (reason) { setGlobalError(reason instanceof Error ? reason.message : "Unable to load ADMIEZO"); return false; }
    finally { setChecking(false); }
  }, [endSession, platformMode]);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    const onExpired = () => { if (!endingSession.current) endSession("Your session has ended. Sign in again."); };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [endSession]);
  useEffect(() => {
    if (!context?.session) return;
    const key = `admiezo-last-activity-${context.session.id}`;
    const timeout = context.session.timeout_minutes * 60_000;
    let lastActivity = Number(sessionStorage.getItem(key)) || Date.now();
    let timer: number;
    let expired = false;
    const expire = () => {
      if (expired) return;
      expired = true;
      sessionStorage.removeItem(key);
      endSession("Your session expired due to inactivity. Sign in again.");
      void csrfFetch("/api/v1/auth/logout", { method: "POST" }).catch(() => {});
    };
    const check = () => {
      if (Date.now() - lastActivity >= timeout) expire();
      else timer = window.setTimeout(check, timeout - (Date.now() - lastActivity));
    };
    const activity = () => {
      if (expired) return;
      if (Date.now() - lastActivity >= timeout) { expire(); return; }
      if (Date.now() - lastActivity < 1000) return;
      lastActivity = Date.now();
      sessionStorage.setItem(key, String(lastActivity));
      window.clearTimeout(timer);
      timer = window.setTimeout(check, timeout);
    };
    const visibility = () => { if (!document.hidden) { window.clearTimeout(timer); check(); } };
    if (!sessionStorage.getItem(key)) sessionStorage.setItem(key, String(lastActivity));
    check();
    for (const event of ["pointerdown", "pointermove", "keydown", "touchstart", "scroll"]) window.addEventListener(event, activity, { passive: true });
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.clearTimeout(timer);
      for (const event of ["pointerdown", "pointermove", "keydown", "touchstart", "scroll"]) window.removeEventListener(event, activity);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [context?.session?.id, context?.session?.timeout_minutes, endSession]);
  async function signOut() { try { await csrfFetch("/api/v1/auth/logout", { method: "POST" }); } finally { endSession(); } }
  useEffect(() => {
    if (!context || context.role !== "platform_admin" || !platformMode) return;
    const restoreStateFromUrlOrStorage = () => {
      const hashKey = window.location.hash.replace("#", "");
      const storedKey = localStorage.getItem(platformStorageKey) || "";
      const next = platformHashToView[hashKey] || platformHashToView[storedKey];
      if (next) setView(next);
    };
    restoreStateFromUrlOrStorage();
    window.addEventListener("hashchange", restoreStateFromUrlOrStorage);
    return () => window.removeEventListener("hashchange", restoreStateFromUrlOrStorage);
  }, [context, platformMode]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);
  async function switchTenant(tenantId: string) {
    const response = await csrfFetch("/api/v1/enterprise/tenants/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tenant_id: tenantId }) });
    if (!response.ok) { const body = await response.json().catch(() => ({})); setGlobalError(body.detail || "University could not be selected"); return; }
    setChecking(true); setView("dashboard");
    if (context?.role === "platform_admin") setPlatformMode(false);
    else await load();
  }
  function rememberPlatformView(next: ViewKey) {
    const hash = platformViewToHash[next];
    if (!hash) return;
    localStorage.setItem(platformStorageKey, hash);
    if (window.location.hash !== `#${hash}`) window.history.replaceState(null, "", `#${hash}`);
  }
  function navigate(next: ViewKey) {
    if (context?.role === "operations_supervisor" && !supervisorViews.has(next)) return;
    if (context?.role === "platform_admin" && platformViews.has(next)) rememberPlatformView(next);
    if (next === "platformAdmin" && context?.role === "platform_admin" && !platformMode) {
      setChecking(true); setPlatformMode(true);
    }
    setView(next); setMenuOpen(false); setToast("");
  }
  if (checking) return <div className="loading-state"><div className="spinner" aria-label="Loading ADMIEZO" /></div>;
  if (!context || !overview) return <Login onSuccess={() => { endingSession.current = false; setLoginNotice(""); void load(); }} notice={loginNotice} />;
  if (context.must_change_password) return <PasswordSetup onComplete={async () => { setChecking(true); await load(); }} onSignOut={signOut} />;
  const role = context.role;
  const visibleNavigation = navigationFor(role, platformMode, context.enabled_modules);
<<<<<<< HEAD
  const activeView = operationalRoleHome[role] || (role === "evaluator" && !evaluatorViews.has(view) ? "evaluation" : view);
=======
  const activeView = intakeDeskViews[role] || (role === "evaluator" && !evaluatorViews.has(view) ? "evaluation" : role === "operations_supervisor" && !supervisorViews.has(view) ? "dashboard" : view);
>>>>>>> main
  const isPlatformAdmin = role === "platform_admin";
  const meta = role === "operations_supervisor" && activeView === "dashboard" ? { ...viewMeta.dashboard, title: "Intake operations", description: "Bundle, packet and script intake status." } : viewMeta[activeView];
  const initials = context.user.name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  async function refreshCurrentView(event?: MouseEvent<HTMLButtonElement>) {
    event?.preventDefault();
    if (refreshing) return;
    setRefreshing(true); setToast("");
    try {
      if (platformMode && isPlatformAdmin) {
        rememberPlatformView(activeView);
        setWorkspaceRefreshToken((value) => value + 1);
      }
      if (activeView === "audit" && platformMode && isPlatformAdmin) {
        setAuditRefreshToken((value) => value + 1);
        setToast("Audit trail reloaded and updated with the most recent immutable actions.");
        return;
      }
      if (!await load()) return;
      if (guidedRefreshViews.has(activeView) || (activeView === "dashboard" && role === "operations_supervisor")) setWorkspaceRefreshToken((value) => value + 1);
      setToast(activeView === "dashboard" ? "Dashboard refreshed with the latest operations data." : "Latest records loaded.");
    } finally {
      setRefreshing(false);
    }
  }
<<<<<<< HEAD
  return <div className="app-shell"><FullPageLocalization />
    <aside className={`sidebar ${menuOpen ? "open" : ""}`}><div className="brand"><span className="brand-mark">A</span><div><div className="brand-name">ADMIEZO</div><div className="brand-label">{t("shell.brandLabel")}</div></div></div><nav className="nav-scroll" aria-label={t("shell.navigation")}>{visibleNavigation.map((group) => <div className="nav-group" key={group.label}><div className="nav-label">{t(`navigation.groups.${group.label.toLowerCase()}`, { defaultValue: group.label })}</div>{group.items.map((item) => <button className={`nav-item ${activeView === item.key ? "active" : ""}`} onClick={() => navigate(item.key)} key={item.key}><item.icon /><span>{t(`navigation.items.${item.label === "Back to control plane" ? "backToControlPlane" : item.key}`, { defaultValue: item.label })}</span></button>)}</div>)}</nav><div className="sidebar-footer"><div className="environment"><span className="environment-dot" />{t("shell.healthy")}</div></div></aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button mobile-menu" title={menuOpen ? t("shell.closeNavigation") : t("shell.openNavigation")} onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X /> : <Menu />}</button><div className="tenant-switch">{platformMode && context.role === "platform_admin" ? <><div className="tenant-name">ADMIEZO Platform</div><div className="session-name">Super administrator control plane</div></> : <>{context.tenants.length > 1 ? <select aria-label={t("shell.activeUniversity")} value={context.tenant.id} onChange={(event) => switchTenant(event.target.value)}>{context.tenants.map((tenant) => <option value={tenant.id} key={tenant.id}>{tenant.name}</option>)}</select> : <div className="tenant-name">{context.tenant.name}</div>}<div className="session-name">{overview.session?.name || t("shell.noSession")}</div></>}</div><div className="top-actions"><LanguageSelector />{!(platformMode && context.role === "platform_admin") && <NotificationCenter onOpenEvaluations={() => navigate("evaluation")} />}<button className="icon-button" title={t("shell.signOut")} onClick={signOut}><LogOut /></button><button className="avatar" title={t("shell.accountSettings")} aria-label={t("shell.accountSettings")} onClick={() => setAccountOpen(true)}>{initials}</button></div></header>
      <main className="content"><header className="page-heading"><div><p className="eyebrow">{t(`views.${activeView}.eyebrow`, { defaultValue: meta.eyebrow })}</p><h1>{t(`views.${activeView}.title`, { defaultValue: meta.title })}</h1><p className="heading-note">{t(`views.${activeView}.description`, { defaultValue: meta.description })}</p></div><button className="secondary-button" onClick={refreshCurrentView} disabled={refreshing}>{refreshing ? <RefreshCw className="spin" /> : <History />}{refreshing ? t("actions.refreshing") : t("actions.refresh")}</button></header>{toast && <div className="toast-stack" aria-live="polite"><div className="app-toast"><Check />{toast}</div></div>}{globalError && <div className="form-error" role="alert">{globalError}</div>}{activeView === "platformAdmin" ? <PlatformAdminWorkspace key={`platform-admin-${workspaceRefreshToken}`} onOpenTenant={switchTenant} /> : activeView === "audit" && platformMode && isPlatformAdmin ? <PlatformAuditWorkspace refreshToken={auditRefreshToken} /> : activeView === "dashboard" ? <Dashboard overview={overview} navigate={navigate} /> : activeView === "configuration" ? <ConfigurationWorkspace /> : activeView === "evaluators" ? <EvaluatorWorkspace /> : activeView === "receiving" ? <ReceivingWorkspace /> : activeView === "custody" ? <CustodyWorkspace /> : activeView === "digitization" ? <DigitizationWorkspace /> : activeView === "anonymisation" ? <AnonymisationWorkspace /> : activeView === "repository" ? <RepositoryWorkspace /> : activeView === "allocation" ? <AllocationWorkspace /> : activeView === "rubrics" ? <RubricWorkspace /> : activeView === "assignmentGovernance" ? <GovernanceWorkspace /> : activeView === "evaluation" ? <EvaluationWorkspace role={role} /> : activeView === "valuation" ? <ValuationWorkspace /> : activeView === "assessmentControl" ? <AdvancedOperationsWorkspace section="assessment" /> : activeView === "liveControl" ? <AdvancedOperationsWorkspace section="operations" /> : activeView === "serviceControl" ? <AdvancedOperationsWorkspace section="services" /> : activeView === "platformControl" ? <AdvancedOperationsWorkspace key={`platform-control-${workspaceRefreshToken}`} section="platform" /> : activeView === "security" ? <SecurityWorkspace /> : activeView === "tenancy" ? <EnterpriseWorkspace role={role} onTenantChange={() => load()} /> : <ModuleWorkspace key={activeView} view={activeView} overview={overview} />}</main>
=======
  return <div className="app-shell">
    <aside className={`sidebar ${menuOpen ? "open" : ""}`}><div className="brand"><span className="brand-mark">A</span><div><div className="brand-name">ADMIEZO</div><div className="brand-label">Evaluation cloud</div></div></div><nav className="nav-scroll" aria-label="Primary navigation">{visibleNavigation.map((group) => <div className="nav-group" key={group.label}><div className="nav-label">{group.label}</div>{group.items.map((item) => <button className={`nav-item ${activeView === item.key ? "active" : ""}`} onClick={() => navigate(item.key)} key={item.key}><item.icon /><span>{item.label}</span></button>)}</div>)}</nav><div className="sidebar-footer"><div className="environment"><span className="environment-dot" />Evaluation core healthy</div></div></aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button mobile-menu" title={menuOpen ? "Close navigation" : "Open navigation"} onClick={() => setMenuOpen(!menuOpen)}>{menuOpen ? <X /> : <Menu />}</button><div className="tenant-switch">{platformMode && context.role === "platform_admin" ? <><div className="tenant-name">ADMIEZO Platform</div><div className="session-name">Super administrator control plane</div></> : <>{context.tenants.length > 1 ? <select aria-label="Active university" value={context.tenant.id} onChange={(event) => switchTenant(event.target.value)}>{context.tenants.map((tenant) => <option value={tenant.id} key={tenant.id}>{tenant.name}</option>)}</select> : <div className="tenant-name">{context.tenant.name}</div>}<div className="session-name">{role === "operations_supervisor" ? "Intake workflow" : overview.session?.name || "No active examination session"}</div></>}</div><div className="top-actions">{!(platformMode && context.role === "platform_admin") && !intakeDeskViews[role] && role !== "operations_supervisor" && <NotificationCenter onOpenEvaluations={() => navigate("evaluation")} />}<button className="icon-button" title="Sign out" onClick={signOut}><LogOut /></button><button className="avatar" title="Account settings" aria-label="Account settings" onClick={() => setAccountOpen(true)}>{initials}</button></div></header>
      <main className="content"><header className="page-heading"><div>{!/^Modules?\s+\d/.test(meta.eyebrow) && <p className="eyebrow">{meta.eyebrow}</p>}<h1>{meta.title}</h1><p className="heading-note">{meta.description}</p></div>
        {activeView !== "valuation" && <button className="secondary-button" onClick={refreshCurrentView} disabled={refreshing}><RefreshCw className={refreshing ? "spin" : undefined} />{refreshing ? "Refreshing..." : "Refresh"}</button>}
      </header>{toast && <div className="toast-stack" aria-live="polite"><div className="app-toast"><Check />{toast}</div></div>}{globalError && <div className="form-error" role="alert">{globalError}</div>}{activeView === "platformAdmin" ? <PlatformAdminWorkspace key={`platform-admin-${workspaceRefreshToken}`} onOpenTenant={switchTenant} /> : activeView === "audit" && platformMode && isPlatformAdmin ? <PlatformAuditWorkspace refreshToken={auditRefreshToken} /> : activeView === "dashboard" ? (role === "operations_supervisor" ? <IntakeOperationsDashboard key={workspaceRefreshToken} onNavigate={navigate} /> : <Dashboard overview={overview} navigate={navigate} />) : activeView === "configuration" ? <ConfigurationWorkspace /> : activeView === "evaluators" ? <EvaluatorWorkspace /> : activeView === "receiving" ? <GuidedIntakeWorkspace key={`receiving-${workspaceRefreshToken}`} stage="receiving" /> : activeView === "custody" ? <GuidedIntakeWorkspace key={`custody-${workspaceRefreshToken}`} stage="custody" /> : activeView === "digitization" ? <GuidedIntakeWorkspace key={`digitization-${workspaceRefreshToken}`} stage="digitization" /> : activeView === "anonymisation" ? <GuidedMaskingWorkspace key={`anonymisation-${workspaceRefreshToken}`} /> : activeView === "repository" ? <RepositoryWorkspace /> : activeView === "allocation" ? <AllocationWorkspace key={`allocation-${workspaceRefreshToken}`} /> : activeView === "rubrics" ? <RubricWorkspace /> : activeView === "assignmentGovernance" ? <GovernanceWorkspace key={`allocation-history-${workspaceRefreshToken}`} /> : activeView === "evaluation" ? <EvaluationWorkspace role={role} /> : activeView === "valuation" ? <ValuationWorkspace /> : activeView === "assessmentControl" ? <AdvancedOperationsWorkspace section="assessment" /> : activeView === "liveControl" ? <AdvancedOperationsWorkspace section="operations" /> : activeView === "serviceControl" ? <AdvancedOperationsWorkspace section="services" /> : activeView === "platformControl" ? <AdvancedOperationsWorkspace key={`platform-control-${workspaceRefreshToken}`} section="platform" /> : activeView === "security" ? <SecurityWorkspace /> : activeView === "tenancy" ? <EnterpriseWorkspace role={role} onTenantChange={async () => { await load(); }} /> : <ModuleWorkspace key={activeView} view={activeView} overview={overview} />}</main>
>>>>>>> main
      {accountOpen && <AccountSettings email={context.user.email} onClose={() => setAccountOpen(false)} />}
    </div>
  </div>;
}
