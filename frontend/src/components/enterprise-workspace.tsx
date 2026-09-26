"use client";

import { Building2, Check, ChevronRight, Network, Pencil, Plus, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { DynamicFields, readDynamicFields, useDynamicFields } from "@/components/dynamic-fields";
import { csrfFetch } from "@/lib/api";

type Institution = { id: string; name: string; code: string; kind: string; parent_id: string | null; policy: Record<string, unknown>; is_active: boolean; version: number };
type Modal = { type: "institution" } | { type: "tenant" } | { type: "edit"; institution: Institution } | null;

async function api(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Enterprise operation failed");
  return body;
}

export function EnterpriseWorkspace({ role, onTenantChange }: { role: string; onTenantChange: () => Promise<void> }) {
  const institutionFields = useDynamicFields("institution");
  const [rows, setRows] = useState<Institution[]>([]);
  const [modal, setModal] = useState<Modal>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try { setRows(await api("/api/v1/enterprise/institutions")); setError(""); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Institution hierarchy could not be loaded"); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  async function submitInstitution(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      await api("/api/v1/enterprise/institutions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: data.get("name"), code: data.get("code"), kind: data.get("kind"), parent_id: data.get("parent_id") || null, policy: { timezone: data.get("timezone") || "Asia/Kolkata" }, custom_fields: readDynamicFields(data, institutionFields) }) });
      setModal(null); setNotice("Institution added to the isolated university hierarchy"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Institution could not be created"); }
    finally { setSaving(false); }
  }

  async function updateInstitution(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!modal || modal.type !== "edit") return;
    setSaving(true); setError(""); setNotice("");
    const data = new FormData(event.currentTarget);
    const isTenantRoot = modal.institution.parent_id === null;
    try {
      await api(`/api/v1/enterprise/institutions/${modal.institution.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: modal.institution.version, name: data.get("name"), policy: { ...modal.institution.policy, timezone: data.get("timezone") || "Asia/Kolkata" }, is_active: isTenantRoot || data.get("is_active") === "on" }) });
      setModal(null); setNotice("Institution updated"); await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Institution could not be updated"); }
    finally { setSaving(false); }
  }

  async function provisionTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const created = await api("/api/v1/enterprise/tenants", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: data.get("name"), code: data.get("code"), admin_email: data.get("admin_email"), admin_first_name: data.get("admin_first_name"), admin_last_name: data.get("admin_last_name"), policy: { timezone: data.get("timezone") || "Asia/Kolkata" } }) });
      await api("/api/v1/enterprise/tenants/switch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ tenant_id: created.id }) });
      setModal(null); await onTenantChange();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "University could not be provisioned"); }
    finally { setSaving(false); }
  }

  return <div className="config-workspace">
    <div className="workspace-toolbar"><div><strong>Institution hierarchy</strong><p>Universities are isolated tenants; campuses and departments inherit local policy.</p></div><div className="header-actions">{role === "platform_admin" && <button className="secondary-button" onClick={() => { setModal({ type: "tenant" }); setError(""); }}><Network />New university</button>}<button className="primary-button" onClick={() => { setModal({ type: "institution" }); setError(""); }}><Plus />Add institution</button></div></div>
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && !modal && <div className="form-error">{error}</div>}
    <section className="panel"><div className="table-wrap"><table><thead><tr><th>Institution</th><th>Code</th><th>Type</th><th>Parent</th><th>Policy</th><th>Status</th><th>Edit</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><div className="institution-name"><Building2 />{row.name}</div></td><td>{row.code}</td><td>{row.kind}</td><td>{rows.find((item) => item.id === row.parent_id)?.name || "Tenant root"}</td><td>{String(row.policy.timezone || "Tenant default")}</td><td><span className={`status-pill ${row.is_active ? "active" : "attention"}`}>{row.is_active ? "Active" : "Inactive"}</span></td><td><button className="icon-button" title="Edit institution" onClick={() => { setModal({ type: "edit", institution: row }); setError(""); }}><Pencil /></button></td></tr>)}</tbody></table></div>{!loading && !rows.length && <div className="empty-state">No institutions configured.</div>}</section>
    {modal && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{modal.type === "tenant" ? "Provision university" : modal.type === "edit" ? "Edit institution" : "Add institution"}</h2><p>{modal.type === "tenant" ? "Creates an isolated tenant and its first administrator." : modal.type === "edit" ? "Update the institution status." : "Creates a policy-aware hierarchy record."}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header>
      {modal.type === "institution" ? <form onSubmit={submitInstitution}><div className="form-grid"><Field name="name" label="Name" required /><Field name="code" label="Code" pattern="[a-z0-9-]+" required /><label className="field"><span>Type</span><select name="kind" required defaultValue="campus"><option value="campus">Campus</option><option value="college">College</option><option value="faculty">Faculty</option><option value="department">Department</option><option value="authority">Authority</option></select></label><label className="field"><span>Parent</span><select name="parent_id" defaultValue=""><option value="">No parent</option>{rows.filter((row) => row.is_active).map((row) => <option value={row.id} key={row.id}>{row.name} · {row.kind}</option>)}</select></label><Field name="timezone" label="Timezone" defaultValue="Asia/Kolkata" required /><DynamicFields fields={institutionFields} /></div><Footer saving={saving} label="Add institution" /></form> : modal.type === "edit" ? <form onSubmit={updateInstitution}><div className="form-grid"><Field name="name" label="Name" defaultValue={modal.institution.name} required /><Field name="code" label="Code" defaultValue={modal.institution.code} disabled /><Field name="timezone" label="Timezone" defaultValue={String(modal.institution.policy.timezone || "Asia/Kolkata")} required /><label className="check-field"><input name="is_active" type="checkbox" defaultChecked={modal.institution.is_active} disabled={modal.institution.parent_id === null} /><span>{modal.institution.parent_id === null ? "Tenant root must stay active" : "Active institution"}</span></label></div><Footer saving={saving} label="Save institution" /></form> : <form onSubmit={provisionTenant}><div className="form-grid"><Field name="name" label="University name" required /><Field name="code" label="Tenant code" pattern="[a-z0-9-]+" required /><Field name="admin_email" label="Administrator email" type="email" required /><Field name="admin_first_name" label="Administrator first name" required /><Field name="admin_last_name" label="Administrator last name" required /><Field name="timezone" label="Timezone" defaultValue="Asia/Kolkata" required /></div><Footer saving={saving} label="Provision and switch" /></form>}
      {error && <div className="form-error">{error}</div>}
    </div></div>}
  </div>;
}

function Field(props: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) { const { label, ...input } = props; return <label className="field"><span>{label}</span><input {...input} /></label>; }
function Footer({ saving, label }: { saving: boolean; label: string }) { return <footer className="modal-footer"><button className="primary-button" disabled={saving}>{saving ? "Saving…" : label}<ChevronRight /></button></footer>; }
