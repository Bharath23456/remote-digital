"use client";

import { Check, ChevronLeft, ChevronRight, CloudUpload, PackageCheck, Plus, ScanLine, Trash2 } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Stage = "receiving" | "custody" | "digitization";
type Paper = { id: string; code: string; title: string };
type PacketDraft = { id: string; barcode: string; paper_id: string; script_barcodes: string };
type Packet = { id: string; barcode: string; subject: string; status: string; expected_scripts: number; scanned_scripts: number; missing_count: number; missing_references: string[]; manual_recognition_enabled?: boolean };
type Bundle = { id: string; barcode: string; source_centre: string; mode: string; status: string; expected_packets: number; received_packets: number; expected_scripts: number; scanned_scripts: number; packets: Packet[] };

const root = "/api/v1/receiving/guided";
const emptyPacket = (): PacketDraft => ({ id: crypto.randomUUID(), barcode: "", paper_id: "", script_barcodes: "" });

async function api(path: string, init?: RequestInit) {
  const response = await csrfFetch(path, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.detail || `Request failed (${response.status})`, response.status);
  return body;
}

class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

const post = (path: string, body: object) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export function GuidedIntakeWorkspace({ stage }: { stage: Stage }) {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [bundleCode, setBundleCode] = useState("");
  const [source, setSource] = useState("");
  const [mode, setMode] = useState<"transfer" | "on_site">("transfer");
  const [drafts, setDrafts] = useState<PacketDraft[]>([emptyPacket()]);
  const [arrivalCode, setArrivalCode] = useState("");
  const [activeBundleCode, setActiveBundleCode] = useState("");
  const [activeBundle, setActiveBundle] = useState<Bundle | null>(null);
  const [packetCode, setPacketCode] = useState("");
  const [packetLookup, setPacketLookup] = useState("");
  const [selectedPacket, setSelectedPacket] = useState("");
  const [selectedPacketBarcode, setSelectedPacketBarcode] = useState("");
  const [activePacket, setActivePacket] = useState<(Packet & { bundle: string }) | null>(null);
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [answerFiles, setAnswerFiles] = useState<File[]>([]);
  const [manualEntryRequired, setManualEntryRequired] = useState(false);
  const [manualQr, setManualQr] = useState("");
  const [manualUsn, setManualUsn] = useState("");
  const [bundlePage, setBundlePage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function clearManualEntry() { setManualEntryRequired(false); setManualQr(""); setManualUsn(""); }

  const load = useCallback(async () => {
    try {
      if (stage !== "digitization") {
        const intake = await api(`${root}/catalog`);
        setBundles(intake.bundles || []);
      }
      if (stage === "receiving") {
        const config = await api(`${root}/papers`);
        setPapers(config.papers || []);
      }
      if (stage === "custody" && activeBundleCode) setActiveBundle(await api(`${root}/lookup/bundles/${encodeURIComponent(activeBundleCode)}`));
      if (stage === "digitization" && selectedPacketBarcode) setActivePacket(await api(`${root}/lookup/packets/${encodeURIComponent(selectedPacketBarcode)}`));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Intake could not be loaded"); }
  }, [stage, activeBundleCode, selectedPacketBarcode]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const packets = drafts.map((item) => ({ barcode: item.barcode.trim(), paper_id: item.paper_id, script_barcodes: item.script_barcodes.split(/[\s,]+/).map((code) => code.trim()).filter(Boolean) }));
    if (packets.some((item) => !item.barcode || !item.paper_id || !item.script_barcodes.length)) { setError("Add a barcode, subject and booklet QR list to every packet"); return; }
    setBusy(true); setError(""); setNotice("");
    let created = false;
    try {
      await post(`${root}/bundles`, { barcode: bundleCode, source_centre: source, mode, packets });
      created = true;
      setBundleCode(""); setSource(""); setDrafts([emptyPacket()]);
      await post(`${root}/bundles/start`, { barcode: bundleCode });
      setNotice(mode === "transfer" ? `Bundle ${bundleCode} dispatched.` : `Bundle ${bundleCode} ready for on-site receipt.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Bundle could not be prepared");
      if (created) setNotice("Bundle saved. Use Start in the bundle list to retry dispatch.");
    } finally { await load(); setBusy(false); }
  }

  async function startBundle(bundle: Bundle) {
    setBusy(true); setError(""); setNotice("");
    try {
      await post(`${root}/bundles/start`, { barcode: bundle.barcode });
      setNotice(`${bundle.barcode} is ready for ${bundle.mode === "on_site" ? "on-site receipt" : "transfer"}.`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Bundle could not be started"); }
    finally { setBusy(false); }
  }

  async function receiveBundle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = arrivalCode.trim().toUpperCase();
    setBusy(true); setError(""); setNotice("");
    try {
      await post(`${root}/bundles/receive`, { barcode: code });
      setActiveBundleCode(code); setArrivalCode("");
      setActiveBundle(await api(`${root}/lookup/bundles/${encodeURIComponent(code)}`));
      setNotice(`${code} received. Scan its packets now.`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Bundle could not be received"); }
    finally { setBusy(false); }
  }

  async function receivePacket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = packetCode.trim().toUpperCase();
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await post(`${root}/packets/receive`, { barcode: code, bundle_barcode: activeBundleCode });
      setPacketCode("");
      setNotice(`${code} received - ${result.subject}, ${result.expected_scripts} scripts expected.`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Packet could not be received"); }
    finally { setBusy(false); }
  }

  async function openPacket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = packetLookup.trim().toUpperCase();
    setBusy(true); setError(""); setNotice("");
    try {
      const match = await api(`${root}/lookup/packets/${encodeURIComponent(code)}`);
      setSelectedPacket(match.id); setSelectedPacketBarcode(code); setActivePacket(match); setPacketLookup(""); clearManualEntry();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Packet could not be opened"); }
    finally { setBusy(false); }
  }

  async function openBundle(code: string) {
    setBusy(true); setError("");
    try { setActiveBundle(await api(`${root}/lookup/bundles/${encodeURIComponent(code)}`)); setActiveBundleCode(code); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Bundle could not be opened"); }
    finally { setBusy(false); }
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const files = coverFile ? [coverFile, ...answerFiles] : [];
    setBusy(true); setError(""); setNotice("");
    try {
      if (!selectedPacket || !files.length) throw new Error("Select a received packet and its front-page image");
      if (files.some((file) => !["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 12_000_000)) throw new Error("Use JPEG, PNG or WebP pages under 12 MB each");
      const cover = new FormData(); cover.append("cover", files[0]);
      if (manualEntryRequired) { cover.append("manual_qr", manualQr.trim()); cover.append("manual_usn", manualUsn.trim()); }
      let script;
      try {
        script = await api(`${root}/packets/${selectedPacket}/recognize`, { method: "POST", body: cover });
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 422 && activePacket?.manual_recognition_enabled && !manualEntryRequired) {
          setManualEntryRequired(true);
          setError(`${reason.message}. Enter the booklet QR and USN from the front page to continue.`);
          return;
        }
        throw reason;
      }
      for (let i = 0; i < files.length; i += 1) {
        const file = files[i];
        const intent = await post("/api/v1/repository/manual-scan/uploads", { script_id: script.script_id, page_number: i + 1, content_type: file.type, maximum_bytes: file.size });
        const stored = await csrfFetch(intent.upload_url, { method: "PUT", headers: intent.headers, body: file });
        if (!stored.ok) throw new Error(`Storage rejected page ${i + 1}`);
        await post(`/api/v1/repository/uploads/${intent.id}/finalize`, { version: intent.version });
      }
      await post(`/api/v1/repository/scripts/${script.script_id}/complete-scan`, { version: script.version, page_count: files.length, location: "Guided manual intake" });
      try {
        await post(`/api/v1/anonymisation/scripts/${script.script_id}/auto-mask`, {});
        setNotice(`${script.script_code} - ${files.length} pages stored and identity cover masked.`);
      } catch (reason) {
        setNotice(`${script.script_code} - ${files.length} pages stored. Masking needs attention in Anonymization.`);
        setError(reason instanceof Error ? reason.message : "Automatic masking did not finish");
      }
      setCoverFile(null); setAnswerFiles([]);
      clearManualEntry();
      form.reset();
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Upload failed"); }
    finally { setBusy(false); }
  }

  const pageCount = Math.max(1, Math.ceil(bundles.length / 10));
  return <div className="config-workspace guided-desk">
    {notice && <div className="success-banner"><Check />{notice}</div>}
    {error && <div className="form-error" role="alert">{error}</div>}

    {stage === "receiving" && <>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Prepare bundle</h2><p className="panel-subtitle">One subject and a booklet QR list per packet.</p></div></header>
        <form onSubmit={create}>
          <div className="form-grid guided-form-body">
            <label className="field"><span>Bundle barcode</span><input value={bundleCode} onChange={(event) => setBundleCode(event.target.value)} required placeholder="BND-2026-001" /></label>
            <label className="field"><span>Source college / centre</span><input value={source} onChange={(event) => setSource(event.target.value)} required /></label>
            <label className="field"><span>Route</span><select value={mode} onChange={(event) => setMode(event.target.value as "transfer" | "on_site")}><option value="transfer">Dispatch to university</option><option value="on_site">On-site scanning</option></select></label>
          </div>
          <div className="guided-packets">{drafts.map((draft, index) => <div className="guided-packet" key={draft.id}>
            <div className="guided-packet-heading"><strong>Packet {index + 1}</strong>{drafts.length > 1 && <button type="button" className="icon-button" title="Remove packet" onClick={() => setDrafts((current) => current.filter((item) => item.id !== draft.id))}><Trash2 /></button>}</div>
            <div className="form-grid"><label className="field"><span>Packet barcode</span><input value={draft.barcode} onChange={(event) => setDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, barcode: event.target.value } : item))} required placeholder="PKT-2026-001" /></label>
              <label className="field"><span>Subject / paper</span><select value={draft.paper_id} onChange={(event) => setDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, paper_id: event.target.value } : item))} required><option value="">Select subject</option>{papers.map((paper) => <option key={paper.id} value={paper.id}>{paper.code} - {paper.title}</option>)}</select></label>
              <label className="field full-field"><span>Expected booklet QR codes</span><textarea value={draft.script_barcodes} onChange={(event) => setDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, script_barcodes: event.target.value } : item))} rows={2} required placeholder="One QR code per line or comma-separated" /></label></div>
          </div>)}</div>
          <footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setDrafts((current) => [...current, emptyPacket()])}><Plus />Packet</button><button className="primary-button" disabled={busy}>{busy ? "Saving..." : mode === "transfer" ? "Create and dispatch" : "Create on-site bundle"}<ChevronRight /></button></footer>
        </form>
      </section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Recent bundles</h2><p className="panel-subtitle">Showing {bundles.length} most recent</p></div></header>
        <div className="table-wrap"><table><thead><tr><th>Bundle</th><th>Source</th><th>Route</th><th>Packets</th><th>Status</th><th>Action</th></tr></thead><tbody>{bundles.slice((bundlePage - 1) * 10, bundlePage * 10).map((bundle) => <tr key={bundle.id}><td><strong>{bundle.barcode}</strong></td><td>{bundle.source_centre}</td><td>{bundle.mode === "on_site" ? "On site" : "Transfer"}</td><td>{bundle.expected_packets}</td><td>{bundle.status.replaceAll("_", " ")}</td><td>{bundle.status === "registered" && <button className="text-button" disabled={busy} onClick={() => void startBundle(bundle)}>Start</button>}</td></tr>)}</tbody></table></div>
        {!bundles.length && <div className="empty-state">No bundles prepared yet.</div>}
        {pageCount > 1 && <div className="guided-pagination"><button className="icon-button" title="Previous page" disabled={bundlePage <= 1} onClick={() => setBundlePage(bundlePage - 1)}><ChevronLeft /></button><span>{bundlePage} / {pageCount}</span><button className="icon-button" title="Next page" disabled={bundlePage >= pageCount} onClick={() => setBundlePage(bundlePage + 1)}><ChevronRight /></button></div>}
      </section>
    </>}

    {stage === "custody" && !activeBundle && <>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Receive bundle</h2><p className="panel-subtitle">Scan the barcode on an arriving bundle.</p></div></header>
        <form className="guided-scan-form" onSubmit={receiveBundle}><label className="field"><span>Bundle barcode</span><input value={arrivalCode} onChange={(event) => setArrivalCode(event.target.value)} required placeholder="Scan or type bundle barcode" autoFocus /></label><button className="primary-button" disabled={busy}><ScanLine />Receive bundle</button></form>
      </section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Recent bundles</h2></div></header><div className="table-wrap"><table><thead><tr><th>Bundle</th><th>Source</th><th>Packets</th><th>Status</th><th>Action</th></tr></thead><tbody>{bundles.filter((bundle) => ["in_transit", "received", "on_site"].includes(bundle.status)).slice(0, 20).map((bundle) => <tr key={bundle.id}><td><strong>{bundle.barcode}</strong></td><td>{bundle.source_centre}</td><td>{bundle.received_packets}/{bundle.expected_packets}</td><td>{bundle.status.replaceAll("_", " ")}</td><td>{["received", "on_site"].includes(bundle.status) && <button className="text-button" disabled={busy} onClick={() => void openBundle(bundle.barcode)}>Open packets</button>}</td></tr>)}</tbody></table></div></section>
    </>}

    {stage === "custody" && activeBundle && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{activeBundle.barcode}</h2><p className="panel-subtitle">{activeBundle.received_packets} of {activeBundle.expected_packets} packets received</p></div><button className="secondary-button" onClick={() => { setActiveBundleCode(""); setActiveBundle(null); setPacketCode(""); }}>Change bundle</button></header>
      <form className="guided-scan-form" onSubmit={receivePacket}><label className="field"><span>Packet barcode</span><input value={packetCode} onChange={(event) => setPacketCode(event.target.value)} required placeholder="Scan or type packet barcode" autoFocus /></label><button className="primary-button" disabled={busy}><PackageCheck />Receive packet</button></form>
      <div className="table-wrap"><table><thead><tr><th>Packet</th><th>Subject</th><th>Expected scripts</th><th>Status</th></tr></thead><tbody>{activeBundle.packets.map((packet) => <tr key={packet.id}><td><strong>{packet.barcode}</strong></td><td>{packet.subject}</td><td>{packet.expected_scripts}</td><td>{packet.status.replaceAll("_", " ")}</td></tr>)}</tbody></table></div>
    </section>}

    {stage === "digitization" && <>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Open packet</h2><p className="panel-subtitle">Scan a received packet before uploading its scripts.</p></div></header>
        <form className="guided-scan-form" onSubmit={openPacket}><label className="field"><span>Packet barcode</span><input value={packetLookup} onChange={(event) => setPacketLookup(event.target.value)} required placeholder="Scan or type packet barcode" autoFocus /></label><button className="primary-button" disabled={busy}><ScanLine />Open packet</button></form>
      </section>
      {activePacket && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{activePacket.barcode}</h2><p className="panel-subtitle">{activePacket.subject} - {activePacket.bundle} - {activePacket.scanned_scripts}/{activePacket.expected_scripts} scripts scanned</p></div><button className="secondary-button" onClick={() => { setSelectedPacket(""); setSelectedPacketBarcode(""); setActivePacket(null); setCoverFile(null); setAnswerFiles([]); clearManualEntry(); }}>Change packet</button></header>
        {activePacket.status === "complete" ? <div className="success-banner"><Check />Packet complete. All expected scripts are scanned.</div> : <form className={`guided-upload-form ${manualEntryRequired ? "manual-recognition" : ""}`} onSubmit={upload}><label className="field"><span>Front page</span><input type="file" accept="image/jpeg,image/png,image/webp" required onChange={(event) => { setCoverFile(event.target.files?.[0] || null); clearManualEntry(); }} /></label><label className="field"><span>Answer pages</span><input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={(event) => setAnswerFiles(Array.from(event.target.files || []).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })))} /></label>{manualEntryRequired && <><label className="field"><span>Booklet QR code</span><input value={manualQr} onChange={(event) => setManualQr(event.target.value)} required autoComplete="off" spellCheck={false} maxLength={64} /></label><label className="field"><span>USN from front page</span><input value={manualUsn} onChange={(event) => setManualUsn(event.target.value.toUpperCase())} required autoComplete="off" spellCheck={false} maxLength={20} /></label></>}<button className="primary-button" disabled={busy || !coverFile || (manualEntryRequired && (!manualQr.trim() || !manualUsn.trim()))}><CloudUpload />{busy ? "Recognizing and uploading..." : manualEntryRequired ? "Confirm details and upload" : "Upload script"}</button></form>}
      </section>}
    </>}
  </div>;
}
