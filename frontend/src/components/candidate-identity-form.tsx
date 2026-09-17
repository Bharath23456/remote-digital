"use client";

import { Check, ChevronRight, Crop, ImagePlus, Plus, X } from "lucide-react";
import { FormEvent, PointerEvent, useEffect, useRef, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Script = { id: string; script_code: string };
type Institution = { id: string; name: string; code: string; kind: string; parent_id: string | null };
type ImageKind = "signature" | "photo";
type Rect = { x: number; y: number; w: number; h: number };

async function api(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Identity operation failed (${response.status})`);
  return body;
}

function ImageField({ kind, file, onFile, onCrop, disabled }: { kind: ImageKind; file: File | null; onFile: (file: File | null) => void; onCrop: () => void; disabled: boolean }) {
  const [preview, setPreview] = useState("");
  useEffect(() => {
    if (!file) { const timer = window.setTimeout(() => setPreview(""), 0); return () => window.clearTimeout(timer); }
    const url = URL.createObjectURL(file);
    const timer = window.setTimeout(() => setPreview(url), 0);
    return () => { window.clearTimeout(timer); URL.revokeObjectURL(url); };
  }, [file]);
  const label = kind === "signature" ? "Signature" : "Photo";
  return <div className="identity-image-field"><div className="identity-image-heading"><strong>{label}</strong>{file && <button type="button" className="icon-button" title={`Remove ${label.toLowerCase()}`} aria-label={`Remove ${label.toLowerCase()}`} onClick={() => { onFile(null); setPreview(""); }}><X /></button>}</div>
    {file && preview && <img className="identity-image-preview" src={preview} alt={`${label} selection`} />}
    <div className="identity-image-actions"><button type="button" className="secondary-button" disabled={disabled} onClick={onCrop}><Crop />Crop page 1</button><label className="secondary-button identity-file-button"><ImagePlus />Upload image<input aria-label={`Upload ${label.toLowerCase()}`} type="file" accept="image/jpeg,image/png,image/webp" disabled={disabled} onChange={(event) => { const next = event.target.files?.[0] || null; if (next) onFile(next); event.currentTarget.value = ""; }} /></label></div>
  </div>;
}

function CropPage({ kind, imageUrl, onUse, onClose }: { kind: ImageKind; imageUrl: string; onUse: (file: File) => void; onClose: () => void }) {
  const image = useRef<HTMLImageElement>(null);
  const origin = useRef<{ x: number; y: number } | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [working, setWorking] = useState(false);
  function point(event: PointerEvent<HTMLDivElement>) {
    const bounds = image.current!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) };
  }
  function pointerDown(event: PointerEvent<HTMLDivElement>) { origin.current = point(event); setRect(null); event.currentTarget.setPointerCapture(event.pointerId); }
  function pointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!origin.current) return;
    const current = point(event);
    setRect({ x: Math.min(origin.current.x, current.x), y: Math.min(origin.current.y, current.y), w: Math.abs(origin.current.x - current.x), h: Math.abs(origin.current.y - current.y) });
  }
  function pointerUp(event: PointerEvent<HTMLDivElement>) { pointerMove(event); origin.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }
  async function cropSelection() {
    const source = image.current;
    if (!source || !rect || rect.w < 0.01 || rect.h < 0.01) return;
    setWorking(true);
    try {
      const sx = Math.round(rect.x * source.naturalWidth);
      const sy = Math.round(rect.y * source.naturalHeight);
      const sw = Math.max(1, Math.round(rect.w * source.naturalWidth));
      const sh = Math.max(1, Math.round(rect.h * source.naturalHeight));
      const scale = Math.min(1, 1600 / sw, 1600 / sh);
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(sw * scale));
      canvas.height = Math.max(1, Math.round(sh * scale));
      canvas.getContext("2d")!.drawImage(source, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/webp", 0.88));
      if (!blob) throw new Error("Crop could not be created");
      onUse(new File([blob], `${kind}-page-1.webp`, { type: "image/webp" }));
    } finally { setWorking(false); }
  }
  return <div className="identity-crop-backdrop" role="dialog" aria-modal="true" aria-label={`Crop ${kind} from first page`}><div className="identity-crop-panel"><header><h3>Crop {kind} from page 1</h3><button type="button" className="icon-button" title="Close crop" onClick={onClose}><X /></button></header><div className="identity-crop-scroll"><div className="identity-crop-image"><img ref={image} src={imageUrl} alt="Unmasked first scanned page" draggable={false} /><div className="identity-crop-hitbox" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} />{rect && <div className="identity-crop-selection" style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }} />}</div></div><footer><button type="button" className="secondary-button" onClick={onClose}>Cancel</button><button type="button" className="primary-button" disabled={!rect || rect.w < 0.01 || rect.h < 0.01 || working} onClick={() => void cropSelection()}><Check />Use crop</button></footer></div></div>;
}

export function CandidateIdentityForm({ scripts, institutions, canManage, onSaved, onOptionsChanged }: { scripts: Script[]; institutions: Institution[]; canManage: boolean; onSaved: () => Promise<void>; onOptionsChanged: () => Promise<void> }) {
  const [options, setOptions] = useState(institutions);
  const [scriptId, setScriptId] = useState("");
  const [institutionId, setInstitutionId] = useState(institutions.find((item) => item.kind === "university")?.id || "");
  const [collegeId, setCollegeId] = useState("");
  const [signature, setSignature] = useState<File | null>(null);
  const [photo, setPhoto] = useState<File | null>(null);
  const [stepUpMethod, setStepUpMethod] = useState<"password" | "code">("password");
  const [stepUpSecret, setStepUpSecret] = useState("");
  const [cropKind, setCropKind] = useState<ImageKind | null>(null);
  const [pageUrl, setPageUrl] = useState("");
  const [addKind, setAddKind] = useState<"college" | "campus" | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => () => { if (pageUrl) URL.revokeObjectURL(pageUrl); }, [pageUrl]);
  function closeCrop() { if (pageUrl) URL.revokeObjectURL(pageUrl); setPageUrl(""); setCropKind(null); }
  function chooseFile(kind: ImageKind, file: File | null) {
    if (file && (!new Set(["image/jpeg", "image/png", "image/webp"]).has(file.type) || file.size > 5_000_000 || file.size < 1)) { setError("Use a JPEG, PNG or WebP image under 5 MB"); return; }
    if (kind === "signature") setSignature(file); else setPhoto(file);
    setError("");
  }
  async function stepUp() {
    if (!stepUpSecret.trim()) return;
    await api("/api/v1/auth/step-up", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [stepUpMethod]: stepUpSecret.trim() }) });
    setStepUpSecret("");
  }
  function actionableError(reason: unknown) {
    const message = reason instanceof Error ? reason.message : "Identity operation failed";
    setError(message.includes("Step-up authentication is required") ? "Enter your password or authenticator code, then try again." : message);
  }
  async function openCrop(kind: ImageKind) {
    if (!scriptId) { setError("Select an anonymous script first"); return; }
    setSaving(true); setError("");
    try {
      await stepUp();
      const page = await api(`/api/v1/anonymisation/scripts/${scriptId}/identity-page`);
      const response = await fetch(page.url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("First scanned page could not be opened");
      if (pageUrl) URL.revokeObjectURL(pageUrl);
      setPageUrl(URL.createObjectURL(await response.blob()));
      setCropKind(kind);
    } catch (reason) { actionableError(reason); } finally { setSaving(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      await stepUp();
      const authorization = await api(`/api/v1/anonymisation/scripts/${scriptId}/identity/authorize`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ purpose: "Candidate identity registration during script digitization", institution_id: institutionId, college_id: collegeId || null }) });
      const payload = new FormData();
      for (const key of ["candidate_name", "register_number", "usn"]) payload.set(key, String(data.get(key) || ""));
      payload.set("institution", options.find((item) => item.id === institutionId)?.name || "");
      payload.set("college", options.find((item) => item.id === collegeId)?.name || "");
      if (signature) payload.set("signature_image", signature);
      if (photo) payload.set("photo_image", photo);
      const response = await csrfFetch(authorization.endpoint, { method: "POST", headers: { Authorization: `Bearer ${authorization.token}` }, body: payload });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Identity service rejected the candidate record");
      await api(`/api/v1/anonymisation/identity-links/${authorization.link_id}/confirm`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ receipt: body.receipt, version: authorization.version }) });
      await onSaved();
    } catch (reason) { actionableError(reason); } finally { setSaving(false); }
  }
  async function addOption(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!addKind) return;
    setSaving(true); setError(""); const data = new FormData(event.currentTarget);
    try {
      const name = String(data.get("name") || "").trim(); const code = String(data.get("code") || "").trim();
      const result = await api("/api/v1/enterprise/institutions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, code, kind: addKind, parent_id: data.get("parent_id"), policy: {}, custom_fields: {} }) });
      const item = { id: result.id, name, code, kind: addKind, parent_id: String(data.get("parent_id")) };
      setOptions((current) => [...current, item]);
      if (addKind === "college") setCollegeId(item.id); else { setInstitutionId(item.id); setCollegeId(""); }
      setAddKind(null);
      await onOptionsChanged();
    } catch (reason) { actionableError(reason); } finally { setSaving(false); }
  }
  const roots = options.filter((item) => item.kind === "university" || item.kind === "campus");
  return <div className="identity-registration">
    <form id="candidate-identity-form" onSubmit={submit}><div className="form-grid">
      <label className="field"><span>Anonymous script</span><select value={scriptId} onChange={(event) => { setScriptId(event.target.value); setSignature(null); setPhoto(null); closeCrop(); }} required><option value="" disabled>Select script</option>{scripts.map((item) => <option value={item.id} key={item.id}>{item.script_code}</option>)}</select></label>
      <label className="field"><span>Candidate name</span><input name="candidate_name" required /></label>
      <label className="field"><span>Register number</span><input name="register_number" required /></label>
      <label className="field"><span>USN</span><input name="usn" /></label>
      <label className="field"><span>College</span><select value={collegeId} onChange={(event) => setCollegeId(event.target.value)}><option value="">Not specified</option>{options.filter((item) => item.kind === "college" && (options.find((root) => root.id === institutionId)?.kind !== "campus" || item.parent_id === institutionId)).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label className="field"><span>Institution</span><select value={institutionId} onChange={(event) => { setInstitutionId(event.target.value); setCollegeId(""); }} required><option value="" disabled>Select institution</option>{roots.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {canManage && <div className="identity-list-actions full-field"><button type="button" className="text-button" onClick={() => setAddKind(addKind === "college" ? null : "college")}><Plus />Add college</button><button type="button" className="text-button" onClick={() => setAddKind(addKind === "campus" ? null : "campus")}><Plus />Add campus</button></div>}
      <ImageField kind="signature" file={signature} onFile={(file) => chooseFile("signature", file)} onCrop={() => void openCrop("signature")} disabled={saving || !scriptId} />
      <ImageField kind="photo" file={photo} onFile={(file) => chooseFile("photo", file)} onCrop={() => void openCrop("photo")} disabled={saving || !scriptId} />
      <div className="identity-step-up full-field"><label className="field"><span>Confirm identity access</span><select value={stepUpMethod} onChange={(event) => setStepUpMethod(event.target.value as "password" | "code")}><option value="password">Account password</option><option value="code">Authenticator code</option></select></label><label className="field"><span>{stepUpMethod === "password" ? "Password" : "Code"}</span><input value={stepUpSecret} onChange={(event) => setStepUpSecret(event.target.value)} type="password" autoComplete={stepUpMethod === "password" ? "current-password" : "one-time-code"} /></label></div>
    </div></form>
    {addKind && <form className="identity-add-option" onSubmit={addOption}><strong>Add {addKind}</strong><div className="form-grid"><label className="field"><span>Name</span><input name="name" required /></label><label className="field"><span>Code</span><input name="code" pattern="[a-z0-9-]+" required /></label><label className="field"><span>Parent</span><select name="parent_id" required defaultValue={addKind === "college" && options.find((item) => item.id === institutionId)?.kind === "campus" ? institutionId : options.find((item) => item.kind === "university")?.id || ""}><option value="" disabled>Select parent</option>{options.filter((item) => item.kind === "university" || (addKind === "college" && item.kind === "campus")).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></div><div className="identity-add-actions"><button type="button" className="secondary-button" onClick={() => setAddKind(null)}>Cancel</button><button className="primary-button" disabled={saving}><Plus />Add {addKind}</button></div></form>}
    {error && <div className="form-error">{error}</div>}
    <footer className="modal-footer"><button className="primary-button" form="candidate-identity-form" disabled={saving || !scriptId || !institutionId}>{saving ? "Working…" : "Encrypt identity"}<ChevronRight /></button></footer>
    {cropKind && pageUrl && <CropPage kind={cropKind} imageUrl={pageUrl} onUse={(file) => { chooseFile(cropKind, file); closeCrop(); }} onClose={closeCrop} />}
  </div>;
}
