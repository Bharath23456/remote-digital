"use client";

import { Archive, Check, ChevronLeft, ChevronRight, Eye, FileCheck2, Fingerprint, RefreshCw, Search, ShieldCheck, Trash2, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Asset = { id: string; script_id: string; script: string; kind: string; page_number: number; sha256: string; byte_size: number; mime_type: string; version: number; retention_until: string | null; legal_hold: boolean; integrity_checked_at: string | null; archived_at: string | null; backup_status: string; replication_status: string };
type Upload = { id: string; script: string; kind: string; page_number: number; asset_version: number; status: string; expires_at: string; sha256: string; byte_size: number; version: number };
type Pagination = { page: number; page_size: number; total: number };
type Catalog = { assets: Asset[]; uploads: Upload[]; asset_pagination: Pagination; upload_pagination: Pagination; summary: { total: number; holds: number; verified: number } };
type Modal = "hold" | "delete" | null;

const emptyCatalog: Catalog = { assets: [], uploads: [], asset_pagination: { page: 1, page_size: 25, total: 0 }, upload_pagination: { page: 1, page_size: 25, total: 0 }, summary: { total: 0, holds: 0, verified: 0 } };
const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
async function api(path: string, options?: RequestInit) { const response = await csrfFetch(path, options); const body = await response.json().catch(() => ({})); if (!response.ok) throw new Error(body.detail || "Repository operation failed"); return body; }

function PageControls({ pagination, onPageChange, onSizeChange }: { pagination: Pagination; onPageChange: (page: number) => void; onSizeChange: (size: number) => void }) {
  const pages = Math.max(1, Math.ceil(pagination.total / pagination.page_size));
  return <div className="repository-pagination"><span>{pagination.total ? `${(pagination.page - 1) * pagination.page_size + 1}-${Math.min(pagination.page * pagination.page_size, pagination.total)} of ${pagination.total}` : "0 records"}</span><label>Rows <select aria-label="Rows per page" value={pagination.page_size} onChange={(event) => onSizeChange(Number(event.target.value))}><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option></select></label><button className="icon-button" title="Previous page" aria-label="Previous page" disabled={pagination.page <= 1} onClick={() => onPageChange(pagination.page - 1)}><ChevronLeft /></button><span>Page {pagination.page} of {pages}</span><button className="icon-button" title="Next page" aria-label="Next page" disabled={pagination.page >= pages} onClick={() => onPageChange(pagination.page + 1)}><ChevronRight /></button></div>;
}

export function RepositoryWorkspace() {
  const [catalog, setCatalog] = useState<Catalog>(emptyCatalog);
  const [tab, setTab] = useState<"assets" | "uploads">("assets");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [assetPage, setAssetPage] = useState(1);
  const [uploadPage, setUploadPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [modal, setModal] = useState<Modal>(null);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const requestSequence = useRef(0);

  useEffect(() => { const timer = window.setTimeout(() => setSearch(query), 250); return () => window.clearTimeout(timer); }, [query]);
  const load = useCallback(async () => {
    const sequence = ++requestSequence.current;
    const params = new URLSearchParams({ asset_page: String(assetPage), upload_page: String(uploadPage), page_size: String(pageSize), q: search });
    try { const repo = await api(`/api/v1/repository/catalog?${params}`); if (sequence === requestSequence.current) { setCatalog(repo); setError(""); } }
    catch (reason) { if (sequence === requestSequence.current) setError(reason instanceof Error ? reason.message : "Repository could not be loaded"); }
    finally { if (sequence === requestSequence.current) setLoading(false); }
  }, [assetPage, uploadPage, pageSize, search]);
  useEffect(() => { const timer = window.setTimeout(() => { void load(); }, 0); return () => window.clearTimeout(timer); }, [load]);

  async function viewAsset(item: Asset) { setError(""); try { const body = await api(`/api/v1/repository/assets/${item.id}/url`); window.open(body.url, "_blank", "noopener,noreferrer"); } catch (reason) { setError(reason instanceof Error ? reason.message : "Asset could not be opened"); } }
  async function verify(item: Asset) { setSaving(true); setError(""); try { const body = await api(`/api/v1/repository/assets/${item.id}/verify`, { method: "POST" }); setNotice(body.valid ? "Repository hash and byte count verified" : "Integrity verification failed and a critical alert was raised"); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Integrity check failed"); } finally { setSaving(false); } }
  async function archive(item: Asset) { setSaving(true); setError(""); try { await api(`/api/v1/repository/assets/${item.id}/archive`, { method: "POST" }); setNotice("Asset archived after storage verified its backup and replica"); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Archive failed"); } finally { setSaving(false); } }
  async function hold(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!selected) return; setSaving(true); setError(""); const d = new FormData(event.currentTarget); try { await api(`/api/v1/repository/assets/${selected.id}/legal-hold`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: d.get("enabled") === "on", retention_until: d.get("retention_until") || null }) }); setModal(null); setSelected(null); setNotice("Legal hold settings updated"); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Legal hold requires a privileged step-up session"); } finally { setSaving(false); } }
  async function secureDelete(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!selected) return; setSaving(true); setError(""); const d = new FormData(event.currentTarget);
    try { await api("/api/v1/auth/step-up", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password: d.get("password") }) }); await api(`/api/v1/repository/assets/${selected.id}/secure-delete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: d.get("reason") }) }); setModal(null); setSelected(null); setNotice("Asset securely deleted and audit evidence recorded"); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Secure deletion could not be completed"); } finally { setSaving(false); }
  }

  const pagination = tab === "assets" ? catalog.asset_pagination : catalog.upload_pagination;
  return <div className="config-workspace">
    <div className="receiving-summary"><div><Archive /><span>Repository pages</span><strong>{catalog.summary.total}</strong></div><div><ShieldCheck /><span>Legal holds</span><strong>{catalog.summary.holds}</strong></div><div><FileCheck2 /><span>Integrity checked</span><strong>{catalog.summary.verified}</strong></div></div>
    <div className="workspace-toolbar"><div className="entity-tabs"><button className={tab === "assets" ? "active" : ""} onClick={() => setTab("assets")}>Assets</button><button className={tab === "uploads" ? "active" : ""} onClick={() => setTab("uploads")}>Upload history</button></div></div>
    <label className="repository-search"><Search /><input value={query} onChange={(event) => { setQuery(event.target.value); setAssetPage(1); setUploadPage(1); }} placeholder="Search anonymous script ID or SHA-256" /></label>
    {notice && <div className="success-banner"><Check />{notice}</div>}{error && !modal && <div className="form-error">{error}</div>}
    <section className="panel">{loading ? <div className="empty-state"><RefreshCw className="spin" /></div> : tab === "assets" ? <div className="table-wrap"><table><thead><tr><th>Script / page</th><th>Copy</th><th>Integrity</th><th>Size</th><th>Retention</th><th>Protection</th><th>Actions</th></tr></thead><tbody>{catalog.assets.map((item) => <tr key={item.id}><td><strong>{item.script}</strong><br /><small>Page {item.page_number} · v{item.version}</small></td><td><span title={item.kind === "thumbnail" ? "Small preview of the same masked page; not a separate answer or identity copy" : undefined}>{titleCase(item.kind)}</span></td><td><code className="hash-value">{item.sha256.slice(0, 14)}…</code><br /><small>{item.integrity_checked_at ? "Verified" : "Not yet checked"}</small></td><td>{(item.byte_size / 1024).toFixed(1)} KB</td><td>{item.retention_until || "—"}</td><td><span className={`status-pill ${item.legal_hold ? "attention" : "ready"}`}>{item.legal_hold ? "Legal hold" : item.kind === "master" ? "Object locked" : "Retained"}</span></td><td><div className="row-actions"><button title="Open this copy with a five-minute signed URL" aria-label={`Open ${item.script} page ${item.page_number}`} onClick={() => viewAsset(item)}><Eye /></button><button title="Compare stored bytes and SHA-256 hash" aria-label="Verify integrity" disabled={saving} onClick={() => verify(item)}><Fingerprint /></button><button title="Set legal hold or retention date" aria-label="Legal hold and retention" onClick={() => { setSelected(item); setModal("hold"); }}><ShieldCheck /></button>{!item.archived_at && <button title="Archive after backup and replica checks" aria-label="Archive asset" disabled={saving} onClick={() => archive(item)}><Archive /></button>}{item.kind !== "master" && !item.legal_hold && item.retention_until && item.retention_until <= new Date().toISOString().slice(0, 10) && <button title="Securely delete expired copy" aria-label="Securely delete asset" onClick={() => { setSelected(item); setModal("delete"); }}><Trash2 /></button>}</div></td></tr>)}</tbody></table></div> : <div className="table-wrap"><table><thead><tr><th>Script</th><th>Kind</th><th>Page / version</th><th>Status</th><th>SHA-256</th><th>Size</th><th>Expires</th></tr></thead><tbody>{catalog.uploads.map((item) => <tr key={item.id}><td>{item.script}</td><td>{titleCase(item.kind)}</td><td>{item.page_number} / v{item.asset_version}</td><td><span className={`status-pill ${item.status}`}>{titleCase(item.status)}</span></td><td><code className="hash-value">{item.sha256 ? `${item.sha256.slice(0, 14)}…` : "—"}</code></td><td>{item.byte_size ? `${(item.byte_size / 1024).toFixed(1)} KB` : "—"}</td><td>{new Date(item.expires_at).toLocaleString("en-IN")}</td></tr>)}</tbody></table></div>}
      {!loading && (tab === "assets" ? !catalog.assets.length : !catalog.uploads.length) && <div className="empty-state">No repository {tab} match this view.</div>}
      {!loading && <PageControls pagination={pagination} onPageChange={tab === "assets" ? setAssetPage : setUploadPage} onSizeChange={(size) => { setPageSize(size); setAssetPage(1); setUploadPage(1); }} />}
    </section>
    {modal && <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{modal === "delete" ? "Securely delete asset" : "Legal hold"}</h2><p>{modal === "delete" ? "This irreversible action requires administrator step-up authentication." : "Control the protection period for this copy."}</p></div><button className="icon-button" title="Close" onClick={() => setModal(null)}><X /></button></header>{modal === "hold" && selected && <form onSubmit={hold}><div className="form-grid one-column"><label className="field check-field"><input name="enabled" type="checkbox" defaultChecked={selected.legal_hold} /><span>Legal hold enabled</span></label><label className="field"><span>Retention until</span><input name="retention_until" type="date" defaultValue={selected.retention_until || ""} /></label></div><Footer saving={saving} label="Update protection" /></form>}{modal === "delete" && selected && <form onSubmit={secureDelete}><div className="form-grid one-column"><div className="impact-warning"><Trash2 /><div><strong>{selected.script} · page {selected.page_number}</strong><span>The encrypted copy and active index entry will be removed.</span></div></div><label className="field"><span>Deletion reason</span><input name="reason" minLength={12} required /></label><label className="field"><span>Administrator password</span><input name="password" type="password" autoComplete="current-password" required /></label></div><Footer saving={saving} label="Securely delete" /></form>}{error && <div className="form-error">{error}</div>}</div></div>}
  </div>;
}

function Footer({ saving, label }: { saving: boolean; label: string }) { return <footer className="modal-footer"><button className="primary-button" disabled={saving}>{saving ? "Working…" : label}<ChevronRight /></button></footer>; }
