"use client";

import { Building2, Check, ChevronRight, CirclePause, Copy, ExternalLink, FormInput, Globe2, KeyRound, Plus, ServerCog, Settings2, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Domain = { id: string; hostname: string; kind: string; status: string; is_primary: boolean; verification_token: string };
type AIPolicy = { mode: "disabled" | "assistive" | "autonomous"; confidence_threshold: number; model_name: string };
type AIProvider = { provider: string; configured: boolean; valid: boolean; available: boolean; message: string; version: number; updated_at: string | null };
type Branding = { name: string; description: string; theme: "forest" | "ocean" | "ruby" | "graphite"; logo_url: string };
type University = { id: string; name: string; code: string; slug: string; status: string; plan: string; enabled_modules: string[]; storage_quota_bytes: number; data_region: string; administrator_count: number; version: number; domains: Domain[]; branding: Branding; ai_policy: AIPolicy; ai_provider: AIProvider; created_at: string };
type Catalog = { summary: { universities: number; active: number; domains: number; administrators: number }; universities: University[] };
type ProvisioningResult = { id: string; name: string; hostname: string; temporary_password: string; administrator_existing: boolean; version: number; branding: Branding };
type FormField = { id: string; form_key: string; key: string; label: string; field_type: string; required: boolean; options: string[]; is_active: boolean; version: number };
type FormCatalog = { forms: { key: string; label: string }[]; fields: FormField[] };
type Modal = "university" | "settings" | "domain" | "fields" | null;

const MODULES = [
  ["configuration", "Exam configuration"], ["evaluators", "Evaluator management"], ["receiving", "Bundle preparation"],
  ["custody", "Chain of custody"], ["digitization", "Digitization"], ["anonymisation", "Anonymization"],
  ["repository", "Script repository"], ["allocation", "Allocation"], ["assignment_governance", "Allocation history"],
  ["rubrics", "Marking schemes"], ["evaluation", "Evaluation"], ["valuation", "Valuation"],
  ["assessment", "Assessment control"], ["operations", "Live operations"], ["services", "Results and services"],
  ["security", "Security governance"], ["audit", "Audit and forensics"], ["enterprise", "University hierarchy"],
];

async function api(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Platform operation failed");
  return body;
}

const title = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const emptyCatalog: Catalog = { summary: { universities: 0, active: 0, domains: 0, administrators: 0 }, universities: [] };
const THEMES: Branding["theme"][] = ["forest", "ocean", "ruby", "graphite"];

export function PlatformAdminWorkspace() {
  const [catalog, setCatalog] = useState<Catalog>(emptyCatalog);
  const [selected, setSelected] = useState<University | null>(null);
  const [modal, setModal] = useState<Modal>(null);
  const [result, setResult] = useState<ProvisioningResult | null>(null);
  const [fieldCatalog, setFieldCatalog] = useState<FormCatalog>({ forms: [], fields: [] });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try { setCatalog(await api("/api/v1/enterprise/control-plane")); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Control plane could not be loaded"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  async function uploadLogo(tenantId: string, version: number, file: File) {
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 2_000_000) throw new Error("Use a PNG, JPEG or WebP logo smaller than 2 MB");
    const intent = await api(`/api/v1/enterprise/tenants/${tenantId}/branding/logo-upload`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content_type: file.type, maximum_bytes: file.size }) });
    const stored = await csrfFetch(intent.upload_url, { method: "PUT", headers: intent.headers, body: file });
    if (!stored.ok) throw new Error("Logo storage upload failed");
    return api(`/api/v1/enterprise/tenants/${tenantId}/branding/logo-finalize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version, storage_key: intent.storage_key, content_type: file.type }) });
  }

  function openUniversityWorkspace(university: University) {
    const domain = university.domains.find((item) => item.is_primary && item.status === "active") || university.domains.find((item) => item.status === "active");
    if (!domain) { setError("This university does not have an active domain"); return; }
    const localPort = window.location.port ? `:${window.location.port}` : "";
    window.location.assign(`${window.location.protocol}//${domain.hostname}${localPort}`);
  }

  async function provision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const payload: Record<string, unknown> = {
        name: data.get("name"), code: data.get("code"), subdomain: data.get("subdomain"), plan: data.get("plan"),
        admin_email: data.get("admin_email"), admin_first_name: data.get("admin_first_name"), admin_last_name: data.get("admin_last_name"),
        storage_quota_gb: Number(data.get("storage_quota_gb")), data_region: data.get("data_region"), policy: { timezone: data.get("timezone") },
        brand_name: data.get("brand_name"), brand_description: data.get("brand_description"), brand_theme: data.get("brand_theme"),
        ai_evaluation_mode: data.get("ai_evaluation_mode"), ai_confidence_threshold: Number(data.get("ai_confidence_threshold")), ai_model_name: data.get("ai_model_name"),
      };
      const apiKey = String(data.get("ai_api_key") || "").trim();
      if (apiKey) payload.ai_api_key = apiKey;
      const created = await api("/api/v1/enterprise/tenants", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const logo = data.get("brand_logo");
      if (logo instanceof File && logo.size) await uploadLogo(created.id, created.version, logo);
      setResult(created); setModal(null); setNotice(`${created.name} is ready on ${created.hostname}`); await load(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "University could not be provisioned"); }
    finally { setSaving(false); }
  }

  async function update(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const payload: Record<string, unknown> = {
        version: selected.version, status: data.get("status"), plan: data.get("plan"), storage_quota_gb: Number(data.get("storage_quota_gb")),
        data_region: data.get("data_region"), enabled_modules: data.getAll("modules"),
        brand_name: data.get("brand_name"), brand_description: data.get("brand_description"), brand_theme: data.get("brand_theme"),
        ai_evaluation_mode: data.get("ai_evaluation_mode"), ai_confidence_threshold: Number(data.get("ai_confidence_threshold")), ai_model_name: data.get("ai_model_name"),
        ai_provider_version: selected.ai_provider.version,
      };
      const apiKey = String(data.get("ai_api_key") || "").trim();
      if (apiKey) payload.ai_api_key = apiKey;
      const updated = await api(`/api/v1/enterprise/tenants/${selected.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const logo = data.get("brand_logo");
      if (logo instanceof File && logo.size) await uploadLogo(selected.id, updated.version, logo);
      setModal(null); setNotice(`${selected.name} settings updated`); await load(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "University settings could not be updated"); }
    finally { setSaving(false); }
  }

  async function addDomain(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return; setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const created = await api(`/api/v1/enterprise/tenants/${selected.id}/domains`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ hostname: data.get("hostname") }) });
      setModal(null); setNotice(`Add TXT ${created.verification_record} = ${created.verification_token}`); await load(false);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Custom domain could not be added"); }
    finally { setSaving(false); }
  }

  async function loadFields(university: University) {
    setSelected(university); setModal("fields"); setError("");
    try { setFieldCatalog(await api(`/api/v1/enterprise/control-plane/form-fields?tenant_id=${university.id}`)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Form fields could not be loaded"); }
  }

  async function createField(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return; setSaving(true); setError("");
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api("/api/v1/enterprise/control-plane/form-fields", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tenant_id: selected.id, form_key: data.get("form_key"), key: data.get("key"), label: data.get("label"), field_type: data.get("field_type"), required: data.get("required") === "on", options: String(data.get("options") || "").split(",").map((item) => item.trim()).filter(Boolean), placeholder: data.get("placeholder"), help_text: data.get("help_text"), sort_order: Number(data.get("sort_order")) }) });
      form.reset(); setNotice("University form field added"); await loadFields(selected);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Form field could not be created"); }
    finally { setSaving(false); }
  }

  async function toggleField(field: FormField) {
    setSaving(true); setError("");
    try { await api(`/api/v1/enterprise/control-plane/form-fields/${field.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: field.version, is_active: !field.is_active }) }); if (selected) await loadFields(selected); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Form field could not be updated"); }
    finally { setSaving(false); }
  }

  function open(kind: Modal, university?: University) { setSelected(university || null); setModal(kind); setError(""); }

  return <div className="config-workspace platform-admin-workspace">
    <div className="control-plane-banner"><div><ServerCog /><div><strong>Platform control plane</strong><span>Tenant lifecycle, domains, service entitlements and university AI governance</span></div></div><div className="header-actions"><button className="primary-button" onClick={() => open("university")}><Plus />New university</button></div></div>
    <div className="receiving-summary"><div><Building2 /><span>Universities</span><strong>{catalog.summary.universities}</strong></div><div><Check /><span>Active</span><strong>{catalog.summary.active}</strong></div><div><Globe2 /><span>Domains</span><strong>{catalog.summary.domains}</strong></div><div><KeyRound /><span>Administrators</span><strong>{catalog.summary.administrators}</strong></div></div>
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && !modal && <div className="form-error" role="alert">{error}</div>}
    {result && <section className="panel credential-panel"><div><KeyRound /><div><strong>Administrator handover</strong><span>{result.administrator_existing ? "The existing account received access." : "Share this temporary password through an approved channel. It is shown only here."}</span></div></div><div><code>{result.hostname}</code>{result.temporary_password && <><code>{result.temporary_password}</code><button className="icon-button" title="Copy temporary password" onClick={() => navigator.clipboard.writeText(result.temporary_password)}><Copy /></button></>}</div><button className="icon-button" title="Dismiss" onClick={() => setResult(null)}><X /></button></section>}
    <section className="panel"><header className="panel-header"><div><h2 className="panel-title">University register</h2><p className="panel-subtitle">Every university has an isolated tenant identifier, brand and domain boundary</p></div></header>{loading ? <div className="empty-state"><div className="spinner" /></div> : <div className="table-wrap platform-university-table"><table><thead><tr><th>University</th><th>Primary domain</th><th>Plan</th><th>AI policy</th><th>Modules</th><th>Storage</th><th>Status</th><th>Controls</th></tr></thead><tbody>{catalog.universities.map((item) => <tr key={item.id}><td data-label="University"><div className="university-brand-cell">{item.branding.logo_url ? <img src={item.branding.logo_url} alt="" /> : <span>{item.branding.name.charAt(0).toUpperCase()}</span>}<div><strong>{item.branding.name}</strong><small>{item.code} · {item.data_region} · {title(item.branding.theme)}</small></div></div></td><td data-label="Domains"><div className="platform-domain-list">{item.domains.map((domain) => <span key={domain.id}><Globe2 />{domain.hostname}<small>{title(domain.status)}</small></span>)}</div></td><td data-label="Plan">{title(item.plan)}</td><td data-label="AI policy"><strong>{title(item.ai_policy.mode)}</strong><br/><small>{item.ai_policy.mode === "disabled" ? (item.ai_provider.configured ? "Key ready" : "Not enabled") : `${item.ai_policy.confidence_threshold}% fallback · ${item.ai_provider.available ? "Key verified" : "Key required"}`}</small></td><td data-label="Modules">{item.enabled_modules.length}</td><td data-label="Storage">{Math.round(item.storage_quota_bytes / 1024 / 1024 / 1024)} GB</td><td data-label="Status"><span className={`status-pill ${item.status}`}>{title(item.status)}</span></td><td data-label="Controls"><div className="row-actions"><button title="Open university workspace" disabled={item.status !== "active"} onClick={() => openUniversityWorkspace(item)}><ExternalLink /></button><button title="University settings" onClick={() => open("settings", item)}><Settings2 /></button><button title="Configure custom form fields" onClick={() => void loadFields(item)}><FormInput /></button><button title="Add custom domain" onClick={() => open("domain", item)}><Globe2 /></button></div></td></tr>)}</tbody></table></div>}{!loading && !catalog.universities.length && <div className="empty-state">No universities have been provisioned.</div>}</section>
    {modal && <div className="modal-backdrop"><div className="modal-panel" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{modal === "university" ? "Provision university" : modal === "settings" ? `Manage ${selected?.name}` : modal === "fields" ? `Form fields · ${selected?.name}` : "Add custom domain"}</h2><p>{modal === "university" ? "Creates the tenant, managed subdomain and first administrator together." : modal === "settings" ? "Changes apply to this university only." : modal === "fields" ? "Add university-specific fields to supported creation forms." : "Custom domains remain inactive until DNS ownership is verified."}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header>
      {modal === "university" ? <form onSubmit={provision}><div className="form-grid"><Field name="name" label="University legal name" required /><Field name="code" label="University code" pattern="[a-z0-9-]+" required /><Field name="subdomain" label="Subdomain" pattern="[a-z0-9-]+" placeholder="northbridge" required /><Field name="admin_email" label="Administrator email" type="email" required /><Field name="admin_first_name" label="First name" required /><Field name="admin_last_name" label="Last name" required /><BrandingFields /><Select name="plan" label="Plan" options={["standard", "professional", "enterprise"]}/><Field name="storage_quota_gb" label="Storage quota (GB)" type="number" min="1" max="10240" defaultValue="10" required /><Field name="data_region" label="Data region" defaultValue="in-primary" required /><Field name="timezone" label="Timezone" defaultValue="Asia/Kolkata" required /><AIGovernanceFields /></div><Footer saving={saving} label="Provision university" /></form> : modal === "settings" && selected ? <form onSubmit={update}><div className="form-grid"><BrandingFields branding={selected.branding} /><Select name="status" label="Lifecycle status" defaultValue={selected.status} options={["active", "suspended", "offboarding"]}/><Select name="plan" label="Plan" defaultValue={selected.plan} options={["standard", "professional", "enterprise"]}/><Field name="storage_quota_gb" label="Storage quota (GB)" type="number" min="1" max="10240" defaultValue={Math.round(selected.storage_quota_bytes / 1024 / 1024 / 1024)} required /><Field name="data_region" label="Data region" defaultValue={selected.data_region} required /><AIGovernanceFields provider={selected.ai_provider} policy={selected.ai_policy} /></div><fieldset className="module-entitlement-grid"><legend>Enabled modules</legend>{MODULES.map(([key, label]) => <label key={key}><input type="checkbox" name="modules" value={key} defaultChecked={selected.enabled_modules.includes(key)}/><span>{label}</span></label>)}</fieldset><Footer saving={saving} label="Save settings" /></form> : modal === "fields" ? <div className="custom-field-manager"><form onSubmit={createField}><div className="form-grid"><label className="field"><span>Form</span><select name="form_key" required>{fieldCatalog.forms.map((form) => <option value={form.key} key={form.key}>{form.label}</option>)}</select></label><Field name="key" label="Field key" pattern="[a-z0-9_-]+" placeholder="faculty_code" required /><Field name="label" label="Label" required /><Select name="field_type" label="Input type" options={["text", "textarea", "number", "date", "select", "checkbox"]}/><Field name="options" label="Select options" placeholder="Option A, Option B" /><Field name="placeholder" label="Placeholder" /><Field name="help_text" label="Help text" /><Field name="sort_order" label="Display order" type="number" min="0" defaultValue="0" /><label className="field check-field"><input name="required" type="checkbox" /><span>Required field</span></label></div><Footer saving={saving} label="Add field" /></form><div className="custom-field-list">{fieldCatalog.fields.map((field) => <div key={field.id}><div><strong>{field.label}</strong><span>{fieldCatalog.forms.find((form) => form.key === field.form_key)?.label} · {title(field.field_type)} · {field.required ? "Required" : "Optional"}</span></div><button className="text-button" disabled={saving} onClick={() => void toggleField(field)}>{field.is_active ? "Disable" : "Enable"}</button></div>)}{!fieldCatalog.fields.length && <div className="compact-empty">No custom fields configured for this university.</div>}</div></div> : <form onSubmit={addDomain}><div className="form-grid single"><Field name="hostname" label="Custom hostname" placeholder="evaluation.university.edu" required /></div><div className="domain-note"><CirclePause />DNS verification instructions are generated after this request.</div><Footer saving={saving} label="Create verification request" /></form>}
      {error && <div className="form-error" role="alert">{error}</div>}
    </div></div>}
  </div>;
}

function Field(props: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) { const { label, ...input } = props; return <label className="field"><span>{label}</span><input {...input}/></label>; }
function Select({ name, label, options, defaultValue }: { name: string; label: string; options: string[]; defaultValue?: string }) { return <label className="field"><span>{label}</span><select name={name} defaultValue={defaultValue || options[0]}>{options.map((option) => <option value={option} key={option}>{title(option)}</option>)}</select></label>; }
function BrandingFields({ branding }: { branding?: Branding }) { return <><Field name="brand_name" label="Display name" defaultValue={branding?.name || ""} placeholder="Shown in this university workspace" /><label className="field"><span>Brand description</span><textarea name="brand_description" rows={3} maxLength={240} defaultValue={branding?.description || ""} placeholder="A short statement shown on the university sign-in page" /></label><label className="field"><span>University logo</span><input name="brand_logo" type="file" accept="image/png,image/jpeg,image/webp" /><small>PNG, JPEG or WebP, up to 2 MB. Leave empty to keep the existing logo.</small></label><fieldset className="theme-picker"><legend>Workspace theme</legend>{THEMES.map((theme) => <label className={`theme-option ${theme}`} key={theme}><input type="radio" name="brand_theme" value={theme} defaultChecked={(branding?.theme || "forest") === theme} /><span className="theme-swatches"><i /><i /><i /></span><strong>{title(theme)}</strong></label>)}</fieldset></>; }
function AIGovernanceFields({ provider, policy }: { provider?: AIProvider; policy?: AIPolicy }) { return <><label className="field"><span>Evaluation mode</span><select name="ai_evaluation_mode" defaultValue={policy?.mode || "disabled"}><option value="disabled">No AI</option><option value="assistive">AI assisted evaluation</option><option value="autonomous">Autonomous AI evaluation</option></select><small>No AI uses evaluators only. Assisted lets evaluators request analysis. Autonomous assigns eligible scripts to ADMIEZO AI Assistant.</small></label><Field name="ai_confidence_threshold" label="Human fallback threshold (%)" type="number" min="1" max="100" step="0.1" defaultValue={policy?.confidence_threshold ?? 85} required /><label className="field"><span>University API key</span><input name="ai_api_key" type="password" autoComplete="new-password" minLength={10} placeholder={provider?.configured ? "Leave blank to keep the current key" : "Required when an AI mode is enabled"} /><small>{provider?.available ? "Verified and encrypted for this university" : "The key is verified before encrypted storage and is never displayed again."}</small></label>{policy ? <label className="field"><span>AI Models</span><input name="ai_model_name" type="text" maxLength={80} defaultValue={policy.model_name} required /><small>Model identifier is checked against this university’s configured AI provider.</small></label> : <input type="hidden" name="ai_model_name" value="admiezo-ai-v1" />}</>; }
function Footer({ saving, label }: { saving: boolean; label: string }) { return <footer className="modal-footer"><button className="primary-button" disabled={saving}>{saving ? "Saving..." : label}<ChevronRight /></button></footer>; }
