"use client";

import { AlertTriangle, Check, History, RefreshCw } from "lucide-react";
import { MouseEvent, useCallback, useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";

type AuditRow = {
  id: string;
  university: string;
  action: string;
  aggregate_type: string;
  aggregate_id: string;
  actor_id: string;
  created_at: string;
};

const readable = (value: string) => value.replaceAll("_", " ").replaceAll(".", " / ");

export function PlatformAuditWorkspace({ refreshToken = 0 }: { refreshToken?: number }) {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadAudit = useCallback(async (signal?: AbortSignal, showNotice = false) => {
    setLoading(true);
    try {
      const response = await csrfFetch("/api/v1/enterprise/control-plane/audit?limit=200", { signal });
      const body = await response.json().catch(() => []);
      if (signal?.aborted) return;
      if (!response.ok) throw new Error(body.detail || "Could not load the platform audit trail");
      setRows(body as AuditRow[]);
      setError("");
      if (showNotice) setNotice("Audit trail reloaded and updated with the most recent immutable actions.");
    } catch (reason) {
      if (!signal?.aborted && reason instanceof Error && reason.name !== "AbortError") setError(reason.message);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void loadAudit(controller.signal, refreshToken > 0), 0);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [loadAudit, refreshToken]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 3200);
    return () => window.clearTimeout(timer);
  }, [notice]);

  function refreshTable(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    void loadAudit(undefined, true);
  }

  if (loading && !rows.length) return <div className="panel empty-state"><div className="spinner" /></div>;
  if (error) return <div className="panel empty-state"><div><AlertTriangle /><strong>{error}</strong></div></div>;
  return <section className="panel">
    <header className="panel-header"><div><h2 className="panel-title">Cross-university events</h2><p className="panel-subtitle">Most recent immutable platform and tenant actions</p></div><span className="status-pill active"><History />{rows.length} events</span></header>
    {notice && <div className="success-banner audit-refresh-notice"><Check />{notice}</div>}
    {rows.length ? <div className="table-wrap"><table><thead><tr><th>University</th><th>Action</th><th>Record</th><th>Actor</th><th>Occurred</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.university}</td><td>{readable(row.action)}</td><td><div className="paper-code">{row.aggregate_type}</div><div className="paper-title">{row.aggregate_id}</div></td><td>{row.actor_id || "System"}</td><td>{new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(row.created_at))}</td></tr>)}</tbody></table></div> : <div className="empty-state"><div><History /><strong>No platform events yet</strong></div></div>}
    <footer className="audit-table-footer"><button className="secondary-button" onClick={refreshTable} disabled={loading}>{loading ? <RefreshCw className="spin" /> : <RefreshCw />}{loading ? "Refreshing..." : "Refresh Table"}</button></footer>
  </section>;
}
