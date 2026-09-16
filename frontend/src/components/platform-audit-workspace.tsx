"use client";

import { AlertTriangle, History } from "lucide-react";
import { useEffect, useState } from "react";
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

export function PlatformAuditWorkspace() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    csrfFetch("/api/v1/enterprise/control-plane/audit?limit=200", { signal: controller.signal })
      .then(async (response) => {
        const body = await response.json().catch(() => []);
        if (!response.ok) throw new Error(body.detail || "Could not load the platform audit trail");
        return body as AuditRow[];
      })
      .then((body) => { setRows(body); setError(""); })
      .catch((reason) => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  if (loading) return <div className="panel empty-state"><div className="spinner" /></div>;
  if (error) return <div className="panel empty-state"><div><AlertTriangle /><strong>{error}</strong></div></div>;
  return <section className="panel">
    <header className="panel-header"><div><h2 className="panel-title">Cross-university events</h2><p className="panel-subtitle">Most recent immutable platform and tenant actions</p></div><span className="status-pill active"><History />{rows.length} events</span></header>
    {rows.length ? <div className="table-wrap"><table><thead><tr><th>University</th><th>Action</th><th>Record</th><th>Actor</th><th>Occurred</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.university}</td><td>{readable(row.action)}</td><td><div className="paper-code">{row.aggregate_type}</div><div className="paper-title">{row.aggregate_id}</div></td><td>{row.actor_id || "System"}</td><td>{new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(row.created_at))}</td></tr>)}</tbody></table></div> : <div className="empty-state"><div><History /><strong>No platform events yet</strong></div></div>}
  </section>;
}
