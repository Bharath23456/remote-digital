"use client";

import { ArrowRight, Boxes, ClipboardCheck, PackageCheck, ScanLine, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Stage = "receiving" | "custody" | "digitization";
type Packet = { barcode: string; subject: string; status: string; expected_scripts: number; scanned_scripts: number };
type Bundle = {
  id: string;
  barcode: string;
  source_centre: string;
  mode: string;
  status: string;
  expected_packets: number;
  received_packets: number;
  expected_scripts: number;
  scanned_scripts: number;
  packets: Packet[];
};

function nextStage(bundle: Bundle): Stage | null {
  if (bundle.status === "registered") return "receiving";
  if (bundle.status === "in_transit" || bundle.received_packets < bundle.expected_packets) return "custody";
  if (bundle.scanned_scripts < bundle.expected_scripts) return "digitization";
  return null;
}

const stageLabel: Record<Stage, string> = {
  receiving: "Dispatch",
  custody: "Receive",
  digitization: "Scan",
};

export function IntakeOperationsDashboard({ onNavigate }: { onNavigate: (stage: Stage) => void }) {
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    csrfFetch("/api/v1/receiving/guided/catalog", { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Intake records could not be loaded");
        setBundles(body.bundles || []);
        setError("");
      })
      .catch((reason) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  const totalPackets = bundles.reduce((sum, bundle) => sum + bundle.expected_packets, 0);
  const receivedPackets = bundles.reduce((sum, bundle) => sum + bundle.received_packets, 0);
  const totalScripts = bundles.reduce((sum, bundle) => sum + bundle.expected_scripts, 0);
  const scannedScripts = bundles.reduce((sum, bundle) => sum + bundle.scanned_scripts, 0);
  const filtered = bundles.filter((bundle) => [bundle.barcode, bundle.source_centre, ...bundle.packets.flatMap((packet) => [packet.barcode, packet.subject])].some((value) => value.toLowerCase().includes(query.toLowerCase().trim())));
  const pageCount = Math.max(1, Math.ceil(filtered.length / 10));

  return <div className="config-workspace">
    <div className="metric-band">
      <div className="metric"><div className="metric-label"><span>Bundles</span><Boxes /></div><div className="metric-value">{bundles.length}</div><div className="metric-detail">{bundles.filter((bundle) => bundle.status === "in_transit").length} in transit</div></div>
      <div className="metric"><div className="metric-label"><span>Packets received</span><PackageCheck /></div><div className="metric-value">{receivedPackets} / {totalPackets}</div><div className="metric-detail">{totalPackets - receivedPackets} pending</div></div>
      <div className="metric"><div className="metric-label"><span>Scripts scanned</span><ScanLine /></div><div className="metric-value">{scannedScripts} / {totalScripts}</div><div className="metric-detail">{totalScripts - scannedScripts} pending</div></div>
      <div className="metric"><div className="metric-label"><span>Complete bundles</span><ClipboardCheck /></div><div className="metric-value">{bundles.filter((bundle) => !nextStage(bundle)).length}</div><div className="metric-detail">All packets and scripts scanned</div></div>
    </div>
    {error && <div className="form-error" role="alert">{error}</div>}
    <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Bundle progress</h2><p className="panel-subtitle">{filtered.length} bundles</p></div><label className="repository-search"><Search /><input aria-label="Find bundle, packet or subject" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Bundle, packet or subject" /></label></header>
      <div className="table-wrap"><table><thead><tr><th>Bundle</th><th>Route</th><th>Packets</th><th>Scripts</th><th>Status</th><th>Action</th></tr></thead><tbody>{filtered.slice((page - 1) * 10, page * 10).map((bundle) => {
        const stage = nextStage(bundle);
        return <tr key={bundle.id}><td><strong>{bundle.barcode}</strong><br /><small>{bundle.source_centre}</small></td><td>{bundle.mode === "on_site" ? "On site" : "Transfer"}</td><td>{bundle.received_packets}/{bundle.expected_packets}</td><td>{bundle.scanned_scripts}/{bundle.expected_scripts}</td><td><span className={`status-pill ${stage || "ready"}`}>{stage === "digitization" ? "Scanning" : stage ? bundle.status.replaceAll("_", " ") : "Complete"}</span></td><td>{stage ? <button className="text-button" onClick={() => onNavigate(stage)}>{stageLabel[stage]}<ArrowRight /></button> : <span>Complete</span>}</td></tr>;
      })}</tbody></table></div>
      {!loading && !filtered.length && <div className="empty-state">No matching bundles.</div>}
      {pageCount > 1 && <div className="guided-pagination"><button className="secondary-button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><span>{page} / {pageCount}</span><button className="secondary-button" disabled={page >= pageCount} onClick={() => setPage(page + 1)}>Next</button></div>}
    </section>
  </div>;
}
