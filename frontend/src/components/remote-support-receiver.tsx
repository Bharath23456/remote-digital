"use client";

import { Check, MonitorUp, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { csrfFetch } from "@/lib/api";
import { playNotificationSound } from "@/lib/notification-sound";

type SupportCommand = {
  id: string;
  sequence: number;
  kind: "previous_page" | "next_page" | "refresh_viewer";
  status: "queued" | "applied" | "failed";
};

type SupportSession = {
  id: string;
  assignment_id: string;
  script: string;
  paper: string;
  requested_by: string;
  reason: string;
  status: "requested" | "active";
  expires_at: string;
  version: number;
  commands: SupportCommand[];
};

type SupportCommandEvent = CustomEvent<{
  assignmentId: string;
  command: SupportCommand;
  complete: (applied: boolean, result: string) => void;
}>;

export const REMOTE_SUPPORT_COMMAND_EVENT = "admiezo:remote-support-command";

export function RemoteSupportReceiver() {
  const [session, setSession] = useState<SupportSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const processed = useRef(new Set<string>());
  const announced = useRef("");

  const acknowledgeCommand = useCallback(async (command: SupportCommand, applied: boolean, result: string) => {
    await csrfFetch(`/api/v1/phase4/remote-support/commands/${command.id}/acknowledge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applied, result }),
    });
  }, []);

  const applyCommand = useCallback((current: SupportSession, command: SupportCommand) => {
    if (processed.current.has(command.id)) return;
    processed.current.add(command.id);
    let completed = false;
    const complete = (applied: boolean, result: string) => {
      if (completed) return;
      completed = true;
      void acknowledgeCommand(command, applied, result);
    };
    window.dispatchEvent(new CustomEvent(REMOTE_SUPPORT_COMMAND_EVENT, {
      detail: { assignmentId: current.assignment_id, command, complete },
    }) as SupportCommandEvent);
    window.setTimeout(() => complete(false, "The evaluator is not currently viewing this assignment."), 1500);
  }, [acknowledgeCommand]);

  const load = useCallback(async () => {
    const response = await csrfFetch("/api/v1/phase4/remote-support/inbox");
    if (!response.ok) return;
    const body = await response.json();
    const current = (body.session || null) as SupportSession | null;
    setSession(current);
    if (current?.status === "requested" && announced.current !== current.id) {
      announced.current = current.id;
      playNotificationSound(true);
    }
    if (current?.status === "active") {
      current.commands.filter((command) => command.status === "queued").forEach((command) => applyCommand(current, command));
    }
  }, [applyCommand]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => void load(), 2500);
    return () => { window.clearTimeout(timer); window.clearInterval(interval); };
  }, [load]);

  async function decide(approve: boolean) {
    if (!session) return;
    setBusy(true); setError("");
    try {
      const response = await csrfFetch(`/api/v1/phase4/remote-support/${session.id}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: session.version, approve }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "The assistance request could not be updated");
      setSession(approve ? body : null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The assistance request could not be updated");
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    if (!session) return;
    setBusy(true); setError("");
    try {
      const response = await csrfFetch(`/api/v1/phase4/remote-support/${session.id}/end`, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Remote assistance could not be ended");
      setSession(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Remote assistance could not be ended");
    } finally {
      setBusy(false);
    }
  }

  if (!session) return null;
  const expires = new Date(session.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return <>
    {session.status === "requested" && <div className="remote-support-backdrop" role="alertdialog" aria-modal="true" aria-labelledby="remote-support-title">
      <section className="remote-support-request">
        <div className="remote-support-symbol"><MonitorUp /></div>
        <div className="remote-support-request-copy">
          <span className="remote-support-kicker">Live assistance request</span>
          <h2 id="remote-support-title">{session.requested_by} wants to help on your screen</h2>
          <p>The administrator can move between pages and refresh this script viewer. They cannot change marks or submit your evaluation.</p>
          <dl><div><dt>Script</dt><dd>{session.script} · {session.paper}</dd></div><div><dt>Reason</dt><dd>{session.reason}</dd></div><div><dt>Request expires</dt><dd>{expires}</dd></div></dl>
          {error && <div className="form-error" role="alert">{error}</div>}
          <div className="remote-support-actions"><button className="secondary-button" disabled={busy} onClick={() => void decide(false)}><X />Decline</button><button className="primary-button" disabled={busy} onClick={() => void decide(true)}><Check />Allow for 10 minutes</button></div>
        </div>
      </section>
    </div>}
    {session.status === "active" && <div className="remote-support-active" role="status">
      <span className="remote-support-live-dot" /><div><strong>{session.requested_by} has temporary viewer control</strong><span>Page navigation and refresh only · ends at {expires}</span></div><button onClick={() => void revoke()} disabled={busy}>End access</button>
    </div>}
  </>;
}

export type { SupportCommandEvent };
