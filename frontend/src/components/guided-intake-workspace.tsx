"use client";

import { Boxes, Check, ChevronLeft, ChevronRight, CloudUpload, PackageCheck, PackagePlus, Printer, QrCode, ScanLine, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";
import { QRCodeSVG } from "qrcode.react";

type Stage = "receiving" | "custody" | "digitization";
type Paper = { id: string; code: string; title: string };
type College = { id: string; code: string; name: string };
type Centre = { id: string; code: string; name: string };
type PreparedPacket = { id: string; barcode: string; qr_value: string; paper_id: string; subject: string; paper_title: string; source_college_id: string; source_college: string; prepared_centre: Centre | null; expected_scripts: number; status: "ready" | "bundled"; version: number; created_at: string };
type Packet = { id: string; barcode: string; qr_value?: string; subject: string; status: string; expected_scripts: number; scanned_scripts: number; missing_count: number; missing_references: string[]; received_centre?: Centre | null; manual_recognition_enabled?: boolean };
type Bundle = { id: string; barcode: string; qr_value: string; source_centre: string; source_college_id?: string | null; prepared_centre: Centre | null; received_centre: Centre | null; mode: string; status: string; expected_packets: number; received_packets: number; expected_scripts: number; scanned_scripts: number; packets: Packet[] };
type PrintableLabel = { kind: "Packet" | "Bundle"; code: string; college: string; centre: string; detail: string };

const root = "/api/v1/receiving/guided";
const centreAssignmentErrors = new Set([
  "Assign this user to an active centre in Access governance before continuing",
  "The assigned centre is not active; update it in Access governance before continuing",
]);

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
  const [colleges, setColleges] = useState<College[]>([]);
  const [preparedPackets, setPreparedPackets] = useState<PreparedPacket[]>([]);
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [preparationStep, setPreparationStep] = useState<"packets" | "bundle">("packets");
  const [currentCentre, setCurrentCentre] = useState<Centre | null>(null);
  const [collegeId, setCollegeId] = useState("");
  const [paperId, setPaperId] = useState("");
  const [bookletScan, setBookletScan] = useState("");
  const [bookletCodes, setBookletCodes] = useState<string[]>([]);
  const [selectedPacketIds, setSelectedPacketIds] = useState<string[]>([]);
  const [printableLabel, setPrintableLabel] = useState<PrintableLabel | null>(null);
  const [mode, setMode] = useState<"transfer" | "on_site">("transfer");
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
  const [centreChecked, setCentreChecked] = useState(false);
  const [centreError, setCentreError] = useState("");
  const [notice, setNotice] = useState("");

  function clearManualEntry() { setManualEntryRequired(false); setManualQr(""); setManualUsn(""); }

  const load = useCallback(async () => {
    setError("");
    if (stage === "receiving") setCentreChecked(false);
    try {
      if (stage === "receiving") {
        const [intakeResult, preparationResult] = await Promise.allSettled([api(`${root}/catalog`), api(`${root}/preparation`)]);
        if (preparationResult.status === "rejected") throw preparationResult.reason;
        const preparation = preparationResult.value;
        setPapers(preparation.papers || []);
        setColleges(preparation.colleges || []);
        setPreparedPackets(preparation.packets || []);
        setCurrentCentre(preparation.centre || null);
        setCentreChecked(true);
        setCentreError(preparation.centre ? "" : preparation.centre_error || "Assign this user to an active centre in Access governance before continuing");
        setCollegeId((current) => current || preparation.colleges?.[0]?.id || "");
        if (intakeResult.status === "fulfilled") setBundles(intakeResult.value.bundles || []);
        else setBundles([]);
        const intakeError = intakeResult.status === "rejected" ? intakeResult.reason?.message || "Intake could not be loaded" : "";
        setError(centreAssignmentErrors.has(intakeError) ? "" : intakeError);
      } else {
        const intake = await api(`${root}/catalog`);
        if (stage !== "digitization") setBundles(intake.bundles || []);
        setCurrentCentre(intake.centre || null);
      }
      if (stage === "custody" && activeBundleCode) setActiveBundle(await api(`${root}/lookup/bundles/${encodeURIComponent(activeBundleCode)}`));
      if (stage === "digitization" && selectedPacketBarcode) setActivePacket(await api(`${root}/lookup/packets/${encodeURIComponent(selectedPacketBarcode)}`));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Intake could not be loaded"); }
  }, [stage, activeBundleCode, selectedPacketBarcode]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);

  function addBookletCodes() {
    const incoming = bookletScan.split(/[\s,]+/).map((code) => code.trim().toUpperCase()).filter(Boolean);
    if (!incoming.length) return;
    const duplicates = incoming.filter((code, index) => bookletCodes.includes(code) || incoming.indexOf(code) !== index);
    if (duplicates.length) { setError(`Booklet QR ${duplicates[0]} is already in this packet`); return; }
    setBookletCodes((current) => [...current, ...incoming]);
    setBookletScan(""); setError("");
  }

  async function savePreparedPacket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!collegeId || !paperId || !bookletCodes.length) { setError("Choose a college and subject, then scan at least one booklet QR"); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const created = await post(`${root}/prepared-packets`, { paper_id: paperId, source_college_id: collegeId, script_barcodes: bookletCodes });
      setPaperId(""); setBookletCodes([]); setBookletScan("");
      setPrintableLabel({ kind: "Packet", code: created.qr_value, college: created.source_college, centre: created.prepared_centre?.name || currentCentre?.name || "", detail: `${created.subject} - ${created.expected_scripts} scripts` });
      setNotice(`${created.barcode} is ready with ${created.expected_scripts} expected scripts.`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Packet could not be prepared"); }
    finally { setBusy(false); }
  }

  async function createBundle(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!collegeId || !selectedPacketIds.length) { setError("Choose a college and at least one ready packet"); return; }
    setBusy(true); setError(""); setNotice("");
    let code = "";
    try {
      const created = await post(`${root}/bundles/from-packets`, { source_college_id: collegeId, mode, packet_ids: selectedPacketIds });
      code = created.barcode;
      await post(`${root}/bundles/start`, { barcode: code });
      setSelectedPacketIds([]);
      setPrintableLabel({ kind: "Bundle", code: created.qr_value, college: colleges.find((college) => college.id === collegeId)?.name || "", centre: created.centre?.name || currentCentre?.name || "", detail: `${selectedPacketIds.length} packets - ${selectedScriptCount} scripts` });
      setNotice(mode === "transfer" ? `Bundle ${code} dispatched to the university.` : `Bundle ${code} opened for on-site scanning.`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Bundle could not be prepared");
      if (code) setNotice(`Bundle ${code} was created. Use Start in Recent bundles to continue.`);
    } finally { setBusy(false); }
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
      setCurrentCentre(match.received_centre || null);
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

  const readyPackets = preparedPackets.filter((packet) => packet.status === "ready");
  const collegeReadyPackets = readyPackets.filter((packet) => packet.source_college_id === collegeId);
  const selectedPackets = collegeReadyPackets.filter((packet) => selectedPacketIds.includes(packet.id));
  const selectedScriptCount = selectedPackets.reduce((total, packet) => total + packet.expected_scripts, 0);
  const pageCount = Math.max(1, Math.ceil(bundles.length / 10));
  return <div className="config-workspace guided-desk">
    {currentCentre && <div className="intake-centre-band"><span>Operating centre</span><strong>{currentCentre.code} - {currentCentre.name}</strong></div>}
    {notice && <div className="success-banner"><Check />{notice}</div>}
    {stage === "receiving" && centreChecked && !currentCentre && centreError && <div className="form-error" role="alert">{centreError}</div>}
    {error && <div className="form-error" role="alert">{error}</div>}

    {stage === "receiving" && <>
      <div className="intake-flow" aria-label="Bundle preparation steps">
        <button type="button" className={preparationStep === "packets" ? "active" : ""} onClick={() => setPreparationStep("packets")}><span>1</span><div><strong>Prepare packets</strong><small>Scan booklet QR codes by subject</small></div></button>
        <ChevronRight aria-hidden="true" />
        <button type="button" className={preparationStep === "bundle" ? "active" : ""} onClick={() => setPreparationStep("bundle")}><span>2</span><div><strong>Create bundle</strong><small>Select ready packets and dispatch</small></div></button>
      </div>

      {!colleges.length && <div className="form-error" role="alert">No colleges are configured. A university administrator must add an active college in Enterprise settings before packets can be prepared.</div>}

      {preparationStep === "packets" && <>
        <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Scan scripts into a packet</h2><p className="panel-subtitle">A packet contains answer booklets for one subject from one college.</p></div><div className="intake-count"><strong>{bookletCodes.length}</strong><span>booklets scanned</span></div></header>
          <form onSubmit={savePreparedPacket}>
            <div className="form-grid guided-form-body">
              <label className="field"><span>College</span><select value={collegeId} onChange={(event) => { setCollegeId(event.target.value); setSelectedPacketIds([]); }} required disabled={!colleges.length}><option value="">Select college</option>{colleges.map((college) => <option key={college.id} value={college.id}>{college.code} - {college.name}</option>)}</select></label>
              <div className="generated-code-note"><QrCode /><div><strong>Packet QR generated automatically</strong><span>A printable label opens after the packet is saved.</span></div></div>
              <label className="field full-field"><span>Subject / paper</span><select value={paperId} onChange={(event) => setPaperId(event.target.value)} required><option value="">Select subject</option>{papers.map((paper) => <option key={paper.id} value={paper.id}>{paper.code} - {paper.title}</option>)}</select></label>
            </div>
            <div className="booklet-scan-area">
              <div className="guided-scan-form">
                <label className="field"><span>Booklet QR code</span><input value={bookletScan} onChange={(event) => setBookletScan(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addBookletCodes(); } }} placeholder="Scan a QR code and press Enter" autoComplete="off" autoFocus /></label>
                <button type="button" className="secondary-button" onClick={addBookletCodes}><ScanLine />Add booklet</button>
              </div>
              {bookletCodes.length ? <div className="booklet-code-list">{bookletCodes.map((code, index) => <div key={code}><span>{index + 1}</span><strong>{code}</strong><button type="button" className="icon-button" title={`Remove ${code}`} onClick={() => setBookletCodes((current) => current.filter((item) => item !== code))}><X /></button></div>)}</div> : <div className="empty-state compact">No booklet QR codes scanned for this packet.</div>}
            </div>
            <footer className="modal-footer"><button type="submit" className="primary-button" disabled={busy || !currentCentre || !colleges.length}>{busy ? "Generating packet..." : "Create packet and QR"}<PackagePlus /></button></footer>
          </form>
        </section>
        <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Ready packets</h2><p className="panel-subtitle">Saved packets can be grouped into a bundle in the next step.</p></div><button type="button" className="secondary-button" disabled={!readyPackets.length} onClick={() => setPreparationStep("bundle")}>Create bundle<ChevronRight /></button></header>
          <div className="table-wrap"><table><thead><tr><th>Packet</th><th>College</th><th>Subject</th><th>Booklets</th><th>Status</th><th>Label</th></tr></thead><tbody>{preparedPackets.slice(0, 30).map((packet) => <tr key={packet.id}><td><strong>{packet.barcode}</strong></td><td>{packet.source_college}</td><td>{packet.subject} - {packet.paper_title}</td><td>{packet.expected_scripts}</td><td>{packet.status === "ready" ? "Ready" : "Bundled"}</td><td><button type="button" className="icon-button" title={`Show QR for ${packet.barcode}`} onClick={() => setPrintableLabel({ kind: "Packet", code: packet.qr_value || packet.barcode, college: packet.source_college, centre: packet.prepared_centre?.name || currentCentre?.name || "", detail: `${packet.subject} - ${packet.expected_scripts} scripts` })}><QrCode /></button></td></tr>)}</tbody></table></div>
          {!preparedPackets.length && <div className="empty-state">No packets prepared yet.</div>}
        </section>
      </>}

      {preparationStep === "bundle" && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Create a bundle from ready packets</h2><p className="panel-subtitle">Only packets prepared for the selected college can be included.</p></div><div className="intake-count"><strong>{selectedPackets.length}</strong><span>packets / {selectedScriptCount} scripts</span></div></header>
        <form onSubmit={createBundle}>
          <div className="form-grid guided-form-body">
            <label className="field"><span>College</span><select value={collegeId} onChange={(event) => { setCollegeId(event.target.value); setSelectedPacketIds([]); }} required disabled={!colleges.length}><option value="">Select college</option>{colleges.map((college) => <option key={college.id} value={college.id}>{college.code} - {college.name}</option>)}</select></label>
            <div className="generated-code-note"><QrCode /><div><strong>Bundle QR generated automatically</strong><span>A printable label opens after creation.</span></div></div>
            <label className="field full-field"><span>Route</span><select value={mode} onChange={(event) => setMode(event.target.value as "transfer" | "on_site")}><option value="transfer">Dispatch to university</option><option value="on_site">Scan at this college</option></select></label>
          </div>
          <div className="packet-selection"><div className="table-wrap"><table><thead><tr><th aria-label="Select packet"></th><th>Packet</th><th>Subject</th><th>Booklets</th><th>Prepared</th></tr></thead><tbody>{collegeReadyPackets.map((packet) => <tr key={packet.id}><td><input type="checkbox" aria-label={`Select ${packet.barcode}`} checked={selectedPacketIds.includes(packet.id)} onChange={(event) => setSelectedPacketIds((current) => event.target.checked ? [...current, packet.id] : current.filter((id) => id !== packet.id))} /></td><td><strong>{packet.barcode}</strong></td><td>{packet.subject} - {packet.paper_title}</td><td>{packet.expected_scripts}</td><td>{new Date(packet.created_at).toLocaleString()}</td></tr>)}</tbody></table></div>{!collegeReadyPackets.length && <div className="empty-state">No ready packets exist for this college. Prepare a packet first.</div>}</div>
          <footer className="modal-footer"><button type="button" className="secondary-button" onClick={() => setPreparationStep("packets")}><ChevronLeft />Prepare packets</button><button type="submit" className="primary-button" disabled={busy || !selectedPacketIds.length}>{busy ? "Generating bundle..." : mode === "transfer" ? "Create QR and dispatch" : "Create on-site bundle QR"}<Boxes /></button></footer>
        </form>
      </section>}

      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Recent bundles</h2><p className="panel-subtitle">Showing {bundles.length} most recent</p></div></header>
        <div className="table-wrap"><table><thead><tr><th>Bundle</th><th>Source</th><th>Route</th><th>Packets</th><th>Status</th><th>Action</th></tr></thead><tbody>{bundles.slice((bundlePage - 1) * 10, bundlePage * 10).map((bundle) => <tr key={bundle.id}><td><strong>{bundle.barcode}</strong></td><td>{bundle.source_centre}</td><td>{bundle.mode === "on_site" ? "On site" : "Transfer"}</td><td>{bundle.expected_packets}</td><td>{bundle.status.replaceAll("_", " ")}</td><td><div className="row-actions"><button type="button" className="icon-button" title={`Show QR for ${bundle.barcode}`} onClick={() => setPrintableLabel({ kind: "Bundle", code: bundle.qr_value || bundle.barcode, college: bundle.source_centre, centre: bundle.prepared_centre?.name || currentCentre?.name || "", detail: `${bundle.expected_packets} packets - ${bundle.expected_scripts} scripts` })}><QrCode /></button>{bundle.status === "registered" && <button className="text-button" disabled={busy} onClick={() => void startBundle(bundle)}>Start</button>}</div></td></tr>)}</tbody></table></div>
        {!bundles.length && <div className="empty-state">No bundles prepared yet.</div>}
        {pageCount > 1 && <div className="guided-pagination"><button className="icon-button" title="Previous page" disabled={bundlePage <= 1} onClick={() => setBundlePage(bundlePage - 1)}><ChevronLeft /></button><span>{bundlePage} / {pageCount}</span><button className="icon-button" title="Next page" disabled={bundlePage >= pageCount} onClick={() => setBundlePage(bundlePage + 1)}><ChevronRight /></button></div>}
      </section>
    </>}

    {stage === "custody" && !activeBundle && <>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Receive bundle</h2><p className="panel-subtitle">Scan the barcode on an arriving bundle.</p></div></header>
        <form className="guided-scan-form" onSubmit={receiveBundle}><label className="field"><span>Bundle barcode</span><input value={arrivalCode} onChange={(event) => setArrivalCode(event.target.value)} required placeholder="Scan or type bundle barcode" autoFocus /></label><button type="submit" className="primary-button" disabled={busy}><ScanLine />Receive bundle</button></form>
      </section>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Recent bundles</h2></div></header><div className="table-wrap"><table><thead><tr><th>Bundle</th><th>Source</th><th>Packets</th><th>Status</th><th>Action</th></tr></thead><tbody>{bundles.filter((bundle) => ["in_transit", "received", "on_site"].includes(bundle.status)).slice(0, 20).map((bundle) => <tr key={bundle.id}><td><strong>{bundle.barcode}</strong></td><td>{bundle.source_centre}</td><td>{bundle.received_packets}/{bundle.expected_packets}</td><td>{bundle.status.replaceAll("_", " ")}</td><td>{["received", "on_site"].includes(bundle.status) && <button className="text-button" disabled={busy} onClick={() => void openBundle(bundle.barcode)}>Open packets</button>}</td></tr>)}</tbody></table></div></section>
    </>}

    {stage === "custody" && activeBundle && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{activeBundle.barcode}</h2><p className="panel-subtitle">{activeBundle.received_packets} of {activeBundle.expected_packets} packets received</p></div><button className="secondary-button" onClick={() => { setActiveBundleCode(""); setActiveBundle(null); setPacketCode(""); }}>Change bundle</button></header>
      <form className="guided-scan-form" onSubmit={receivePacket}><label className="field"><span>Packet barcode</span><input value={packetCode} onChange={(event) => setPacketCode(event.target.value)} required placeholder="Scan or type packet barcode" autoFocus /></label><button type="submit" className="primary-button" disabled={busy}><PackageCheck />Receive packet</button></form>
      <div className="table-wrap"><table><thead><tr><th>Packet</th><th>Subject</th><th>Expected scripts</th><th>Status</th></tr></thead><tbody>{activeBundle.packets.map((packet) => <tr key={packet.id}><td><strong>{packet.barcode}</strong></td><td>{packet.subject}</td><td>{packet.expected_scripts}</td><td>{packet.status.replaceAll("_", " ")}</td></tr>)}</tbody></table></div>
    </section>}

    {stage === "digitization" && <>
      <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Open packet</h2><p className="panel-subtitle">Scan a received packet before uploading its scripts.</p></div></header>
        <form className="guided-scan-form" onSubmit={openPacket}><label className="field"><span>Packet barcode</span><input value={packetLookup} onChange={(event) => setPacketLookup(event.target.value)} required placeholder="Scan or type packet barcode" autoFocus /></label><button type="submit" className="primary-button" disabled={busy}><ScanLine />Open packet</button></form>
      </section>
      {activePacket && <section className="panel"><header className="panel-header"><div><h2 className="panel-title">{activePacket.barcode}</h2><p className="panel-subtitle">{activePacket.subject} - {activePacket.bundle} - {activePacket.scanned_scripts}/{activePacket.expected_scripts} scripts scanned</p></div><button className="secondary-button" onClick={() => { setSelectedPacket(""); setSelectedPacketBarcode(""); setActivePacket(null); setCoverFile(null); setAnswerFiles([]); clearManualEntry(); }}>Change packet</button></header>
        {activePacket.status === "complete" ? <div className="success-banner"><Check />Packet complete. All expected scripts are scanned.</div> : <form className={`guided-upload-form ${manualEntryRequired ? "manual-recognition" : ""}`} onSubmit={upload}><label className="field"><span>Front page</span><input type="file" accept="image/jpeg,image/png,image/webp" required onChange={(event) => { setCoverFile(event.target.files?.[0] || null); clearManualEntry(); }} /></label><label className="field"><span>Answer pages</span><input type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={(event) => setAnswerFiles(Array.from(event.target.files || []).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })))} /></label>{manualEntryRequired && <><label className="field"><span>Booklet QR code</span><input value={manualQr} onChange={(event) => setManualQr(event.target.value)} required autoComplete="off" spellCheck={false} maxLength={64} /></label><label className="field"><span>USN from front page</span><input value={manualUsn} onChange={(event) => setManualUsn(event.target.value.toUpperCase())} required autoComplete="off" spellCheck={false} maxLength={20} /></label></>}<button type="submit" className="primary-button" disabled={busy || !coverFile || (manualEntryRequired && (!manualQr.trim() || !manualUsn.trim()))}><CloudUpload />{busy ? "Recognizing and uploading..." : manualEntryRequired ? "Confirm details and upload" : "Upload script"}</button></form>}
      </section>}
    </>}

    {printableLabel && <div className="modal-backdrop"><section className="modal-panel compact intake-label-modal" role="dialog" aria-modal="true" aria-label={`${printableLabel.kind} QR label`}><header className="modal-header no-print"><div><h2>{printableLabel.kind} QR ready</h2><p>Print and attach this label before handover.</p></div><button type="button" className="icon-button" title="Close" onClick={() => setPrintableLabel(null)}><X /></button></header><div className="intake-label-print"><div className="intake-label-heading"><strong>ADMIEZO</strong><span>{printableLabel.kind.toUpperCase()}</span></div><QRCodeSVG value={printableLabel.code} size={220} level="M" includeMargin /><code>{printableLabel.code}</code><dl><div><dt>College</dt><dd>{printableLabel.college}</dd></div><div><dt>Centre</dt><dd>{printableLabel.centre}</dd></div><div><dt>Contents</dt><dd>{printableLabel.detail}</dd></div></dl></div><footer className="modal-footer no-print"><button type="button" className="secondary-button" onClick={() => setPrintableLabel(null)}>Close</button><button type="button" className="primary-button" onClick={() => window.print()}><Printer />Print label</button></footer></section></div>}
  </div>;
}
