"use client";

import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";

type HistoryRow = {
  id: string;
  run_id: string;
  ran_by: string;
  ran_at: string;
  script: string;
  paper: string;
  subject: string;
  round: number;
  evaluator: string | null;
  status: string;
  blockers: string[];
};

export function GovernanceWorkspace() {
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await csrfFetch(`/api/v1/allocation/history?page=${page}&q=${encodeURIComponent(query.trim())}`);
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Allocation history could not be loaded");
      setRows(body.rows || []);
      setTotal(body.total ?? body.rows?.length ?? 0);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Allocation history could not be loaded");
    } finally {
      setLoading(false);
    }
  }, [page, query]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), query ? 250 : 0); return () => window.clearTimeout(timer); }, [load, query]);

  return <div className="config-workspace">
    <div className="workspace-toolbar">
      <label className="search-field"><Search /><input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="Search script, subject or evaluator" aria-label="Search allocation history" /></label>
    </div>
    {error && <div className="form-error" role="alert">{error}</div>}
    <section className="panel"><header className="panel-header"><div><h2 className="panel-title">Allocation history</h2><p className="panel-subtitle">Simulation operator, subject and proposed evaluator for each anonymous script</p></div></header>
      {loading ? <div className="empty-state">Loading allocation history…</div> : rows.length ? <><div className="table-wrap"><table><thead><tr><th>Run by</th><th>Time</th><th>Script</th><th>Paper / subject</th><th>Round</th><th>Evaluator</th><th>Status</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong>{row.ran_by}</strong></td><td>{new Date(row.ran_at).toLocaleString("en-IN")}</td><td><strong>{row.script}</strong></td><td>{row.paper}<br /><small>{row.subject}</small></td><td>{row.round}</td><td>{row.evaluator || "Not matched"}</td><td>{row.blockers.length ? <span className="allocation-blockers">{row.blockers.join(", ")}</span> : <span className={`status-pill ${row.status}`}>{row.status === "planned" ? "Proposed" : row.status === "completed" ? "Committed" : row.status === "partial" ? "Run partially committed" : "Unallocated"}</span>}</td></tr>)}</tbody></table></div><div className="repository-pagination"><span>{(page - 1) * 50 + 1}–{Math.min(page * 50, total)} of {total}</span><button className="icon-button" title="Previous page" disabled={page === 1} onClick={() => setPage(page - 1)}><ChevronLeft /></button><button className="icon-button" title="Next page" disabled={page * 50 >= total} onClick={() => setPage(page + 1)}><ChevronRight /></button></div></> : <div className="empty-state">{query ? "No matching allocation records." : "No simulations recorded yet."}</div>}
    </section>
  </div>;
}
