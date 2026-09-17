"use client";

import { Check, KeyRound, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { csrfFetch } from "@/lib/api";

type SecurityContext = {
  methods: { kind: string; active: boolean }[];
  policy: { require_mfa: boolean };
};

export function AccountSettings({ email, onClose }: { email: string; onClose: () => void }) {
  const [security, setSecurity] = useState<SecurityContext | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    csrfFetch("/api/v1/auth/security-context")
      .then(async (response) => response.ok ? response.json() : null)
      .then((value) => setSecurity(value))
      .catch(() => setSecurity(null));
  }, []);

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const currentPassword = String(data.get("current_password") || "");
    const newPassword = String(data.get("new_password") || "");
    if (newPassword !== data.get("confirm_password")) { setError("New passwords do not match"); return; }
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await csrfFetch("/api/v1/auth/password/change", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || "Password could not be changed");
      form.reset();
      setNotice("Password changed. Other sessions have been signed out.");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Password could not be changed"); }
    finally { setBusy(false); }
  }

  const authenticatorEnabled = security?.methods.some((method) => method.kind === "totp" && method.active);
  return <div className="modal-backdrop"><div className="modal-panel compact" role="dialog" aria-modal="true" aria-label="Account settings">
    <header className="modal-header"><div><h2>Account settings</h2><p>{email}</p></div><button className="icon-button" title="Close" onClick={onClose}><X /></button></header>
    {security && <div className="account-auth-status"><KeyRound /><div><strong>Authenticator app</strong><span>{authenticatorEnabled ? "Enabled" : security.policy.require_mfa ? "Required at next sign-in" : "Not enrolled"}</span></div></div>}
    <form onSubmit={changePassword}><div className="form-grid one-column"><label className="field"><span>Current password</span><input name="current_password" type="password" autoComplete="current-password" required /></label><label className="field"><span>New password</span><input name="new_password" type="password" autoComplete="new-password" minLength={12} required /></label><label className="field"><span>Confirm new password</span><input name="confirm_password" type="password" autoComplete="new-password" minLength={12} required /></label></div>
      {error && <div className="form-error" role="alert">{error}</div>}{notice && <div className="success-banner"><Check />{notice}</div>}
      <footer className="modal-footer"><button type="button" className="secondary-button" onClick={onClose}>Close</button><button className="primary-button" disabled={busy}><KeyRound />{busy ? "Updating..." : "Change password"}</button></footer>
    </form>
  </div></div>;
}
