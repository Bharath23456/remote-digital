export async function csrfFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const method = (init.method || "GET").toUpperCase();
  const url = typeof input === "string" ? input : input.toString();
  const protectedWrite = url.startsWith("/api/") && !["GET", "HEAD", "OPTIONS", "TRACE"].includes(method);
  const headers = new Headers(init.headers);
  const secureSessionId = typeof window !== "undefined" ? sessionStorage.getItem("admiezo-secure-evaluation-id") : null;
  if (url.startsWith("/api/") && secureSessionId && !headers.has("X-Secure-Evaluation-Session")) {
    headers.set("X-Secure-Evaluation-Session", secureSessionId);
  }
  if (protectedWrite) {
    const tokenResponse = await fetch("/api/v1/auth/csrf", { credentials: "same-origin" });
    const contentType = tokenResponse.headers.get("content-type") || "";
    const tokenBody = contentType.includes("application/json")
      ? await tokenResponse.json().catch(() => ({}))
      : {};
    if (!tokenResponse.ok || !tokenBody.csrf_token) {
      const detail = typeof tokenBody.detail === "string" ? tokenBody.detail : "";
      if (tokenResponse.status === 401) throw new Error("Your session expired. Sign in again before continuing.");
      throw new Error(detail || `Security token initialization failed (${tokenResponse.status}). Refresh and try again.`);
    }
    headers.set("X-CSRFToken", tokenBody.csrf_token);
    const normalized = new URL(url, window.location.origin).pathname;
    if ((normalized.endsWith("/submit") || normalized.endsWith("/finalize")) && !headers.has("Idempotency-Key")) {
      headers.set("Idempotency-Key", crypto.randomUUID());
    }
  }
  return fetch(input, { ...init, headers, credentials: init.credentials || "same-origin" });
}
