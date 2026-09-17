"use client";

import { Camera, Check, LockKeyhole, Monitor, RefreshCw, ShieldAlert, Video, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { csrfFetch } from "@/lib/api";

type Policy = { camera_required: boolean; fullscreen_required: boolean; single_screen_required: boolean; event_recording: boolean; heartbeat_seconds: number; no_face_seconds: number; retention_days: number };
type Session = { id: string; assignment_id: string; status: string; pause_reason: string; violation_count: number; policy: Policy; version: number };
type Preflight = { camera_ready: boolean; fullscreen_active: boolean; screen_count: number | null; screen_check_supported: boolean; video_inputs: number; audio_inputs: number; inventory_digest: string };
type Inventory = { video_inputs: number; audio_inputs: number; audio_outputs: number; digest: string };
type BufferedChunk = { blob: Blob; startedAt: Date; endedAt: Date };
type FaceDetectorLike = { detect(source: CanvasImageSource): Promise<unknown[]> };
type FaceDetectorConstructor = new (options?: { fastMode?: boolean; maxDetectedFaces?: number }) => FaceDetectorLike;

class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}

async function request(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.detail || "Secure evaluation operation failed", response.status);
  return body;
}

async function digest(value: string | ArrayBuffer) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function mediaInventory(): Promise<{ public: Inventory; rawDigest: string }> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const normalized = devices.map((item) => `${item.kind}:${item.deviceId}:${item.groupId}`).sort().join("|");
  const rawDigest = await digest(normalized);
  return {
    rawDigest,
    public: {
      video_inputs: devices.filter((item) => item.kind === "videoinput").length,
      audio_inputs: devices.filter((item) => item.kind === "audioinput").length,
      audio_outputs: devices.filter((item) => item.kind === "audiooutput").length,
      digest: rawDigest,
    },
  };
}

async function availableScreens() {
  const managedWindow = window as typeof window & { getScreenDetails?: () => Promise<{ screens: unknown[] }> };
  if (!managedWindow.getScreenDetails) return { supported: false, count: null as number | null };
  try {
    const details = await managedWindow.getScreenDetails();
    return { supported: true, count: details.screens.length };
  } catch {
    return { supported: true, count: null as number | null };
  }
}

function cameraIsActive(stream: MediaStream | null) {
  return Boolean(stream?.getVideoTracks().some((track) => track.readyState === "live" && track.enabled && !track.muted));
}

export function useSecureEvaluationSession() {
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [paused, setPaused] = useState(false);
  const [pauseReason, setPauseReason] = useState("");
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");
  const [needsReauthentication, setNeedsReauthentication] = useState(false);
  const sessionRef = useRef<Session | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const preflightRef = useRef<Preflight | null>(null);
  const inventoryRef = useRef("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const evidenceReasonRef = useRef("");
  const chunksRef = useRef<BufferedChunk[]>([]);
  const sequenceRef = useRef(0);
  const closingRef = useRef(false);
  const eventInFlightRef = useRef(new Set<string>());

  const updateSession = useCallback((next: Session) => {
    sessionRef.current = next; setSession(next);
    setPaused(next.status === "paused");
    setPauseReason(next.pause_reason || "security_policy");
  }, []);

  const posture = useCallback(async (deviceChanged = false) => {
    const screens = await availableScreens();
    return {
      camera_active: cameraIsActive(streamRef.current),
      fullscreen_active: Boolean(document.fullscreenElement),
      screen_count: screens.count,
      screen_check_supported: screens.supported,
      device_changed: deviceChanged,
      online: navigator.onLine,
    };
  }, []);

  const uploadEvidence = useCallback(async (reason: string, chunks: BufferedChunk[]) => {
    const current = sessionRef.current;
    if (!current || !chunks.length || !current.policy.event_recording) return;
    const mimeType = chunks.find((item) => item.blob.type)?.blob.type || "video/webm";
    const blob = new Blob(chunks.map((item) => item.blob), { type: mimeType });
    if (!blob.size || blob.size > 20_000_000) return;
    const sequence = ++sequenceRef.current;
    try {
      const intent = await request(`/api/v1/phase4/remote-security/sessions/${current.id}/evidence`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sequence, reason, mime_type: mimeType, captured_from: chunks[0].startedAt.toISOString(), captured_to: chunks.at(-1)?.endedAt.toISOString(), max_bytes: Math.min(20_000_000, blob.size + 1024) }) });
      const upload = await fetch(intent.upload_url, { method: "PUT", headers: { "Content-Type": mimeType }, body: blob });
      if (!upload.ok) throw new Error("Evidence upload failed");
      const sha256 = await digest(await blob.arrayBuffer());
      await request(`/api/v1/phase4/remote-security/evidence/${intent.id}/complete`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sha256, byte_size: blob.size }) });
    } catch {
      setError("Security evidence upload is waiting for a stable connection.");
    }
  }, []);

  const pause = useCallback((category: string) => { setPaused(true); setPauseReason(category); setChecking(true); evidenceReasonRef.current ||= category; }, []);

  const report = useCallback(async (category: string, severity: "low" | "medium" | "high" | "critical", details: Record<string, unknown> = {}) => {
    const current = sessionRef.current;
    if (!current || closingRef.current || eventInFlightRef.current.has(category)) return;
    const shouldPause = severity === "critical" || ["viewer_hidden", "fullscreen_exited", "camera_stopped", "external_media_device", "multiple_screens", "multiple_faces", "camera_obstructed"].includes(category);
    if (shouldPause) pause(category);
    eventInFlightRef.current.add(category);
    try {
      const result = await request("/api/v1/phase4/remote-security/events", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assignment_id: current.assignment_id, secure_session_id: current.id, category, severity, device_fingerprint: preflightRef.current?.inventory_digest || "0".repeat(64), session_fingerprint: sessionStorage.getItem("admiezo-secure-session-fingerprint") || "0".repeat(64), details }) });
      if (result.session) updateSession(result.session);
      if (result.action === "pause") { setPaused(true); setPauseReason(category); }
    } catch {
      if (shouldPause) setError("The session remains paused until the security event is acknowledged by the server.");
    } finally {
      if (shouldPause) setChecking(false);
      window.setTimeout(() => eventInFlightRef.current.delete(category), 3000);
    }
  }, [pause, updateSession]);

  const prepare = useCallback(async () => {
    setChecking(true); setError(""); setPreflight(null);
    try {
      const currentPolicy = await request("/api/v1/phase4/remote-security/policy") as Policy;
      setPolicy(currentPolicy);
      const camera = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = camera; setStream(camera);
      const inventory = await mediaInventory();
      const screens = await availableScreens();
      inventoryRef.current = inventory.rawDigest;
      const result = { camera_ready: cameraIsActive(camera), fullscreen_active: false, screen_count: screens.count, screen_check_supported: screens.supported, video_inputs: inventory.public.video_inputs, audio_inputs: inventory.public.audio_inputs, inventory_digest: inventory.public.digest };
      preflightRef.current = result; setPreflight(result);
      if (currentPolicy.single_screen_required && screens.count !== null && screens.count > 1) setError("Disconnect additional displays before starting evaluation.");
    } catch (reason) {
      const message = reason instanceof Error && reason.name === "NotAllowedError" ? "Webcam permission is required for secure evaluation." : reason instanceof Error ? reason.message : "Security checks could not be completed";
      setError(message);
    } finally { setChecking(false); }
  }, []);

  const start = useCallback(async (assignmentId: string, consent: boolean) => {
    if (!policy || !preflightRef.current || !streamRef.current) throw new Error("Run the security checks first");
    setChecking(true); setError(""); closingRef.current = false;
    try {
      if (policy.fullscreen_required && !document.fullscreenElement) await document.documentElement.requestFullscreen();
      const screens = await availableScreens();
      const currentPreflight = { ...preflightRef.current, fullscreen_active: Boolean(document.fullscreenElement), screen_count: screens.count };
      const sessionFingerprint = await digest(`${crypto.randomUUID()}:${Date.now()}:${assignmentId}`);
      const deviceFingerprint = await digest([navigator.userAgent, screen.width, screen.height, Intl.DateTimeFormat().resolvedOptions().timeZone, inventoryRef.current].join("|"));
      const inventory = await mediaInventory();
      const created = await request("/api/v1/phase4/remote-security/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assignment_id: assignmentId, session_fingerprint: sessionFingerprint, device_fingerprint: deviceFingerprint, consent, preflight: currentPreflight, device_inventory: inventory.public }) }) as Session;
      sessionStorage.setItem("admiezo-secure-session-fingerprint", sessionFingerprint);
      sessionStorage.setItem("admiezo-secure-evaluation-id", created.id);
      preflightRef.current = { ...currentPreflight, inventory_digest: deviceFingerprint };
      updateSession(created);
      return created;
    } catch (reason) {
      if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
      const message = reason instanceof Error ? reason.message : "Secure evaluation could not start";
      setError(message); throw reason;
    } finally { setChecking(false); }
  }, [policy, updateSession]);

  const resume = useCallback(async (password = "") => {
    const current = sessionRef.current;
    if (!current) return false;
    setChecking(true); setError("");
    try {
      if (!cameraIsActive(streamRef.current)) {
        const replacement = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        streamRef.current = replacement; setStream(replacement);
      }
      if (current.policy.fullscreen_required && !document.fullscreenElement) await document.documentElement.requestFullscreen();
      if (password) await request("/api/v1/auth/step-up", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password, code: "" }) });
      const next = await request(`/api/v1/phase4/remote-security/sessions/${current.id}/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ posture: await posture(false) }) }) as Session;
      updateSession(next); setNeedsReauthentication(false); return true;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 428) setNeedsReauthentication(true);
      setError(reason instanceof Error ? reason.message : "Secure session could not resume"); return false;
    } finally { setChecking(false); }
  }, [posture, updateSession]);

  const finish = useCallback(async (completed: boolean) => {
    const current = sessionRef.current;
    closingRef.current = true;
    try {
      if (current) await request(`/api/v1/phase4/remote-security/sessions/${current.id}/finish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ completed }) });
    } catch { /* Closing the local viewer must continue even if the network is gone. */ }
    sessionStorage.removeItem("admiezo-secure-evaluation-id");
    sessionStorage.removeItem("admiezo-secure-session-fingerprint");
    recorderRef.current?.stop(); recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop()); streamRef.current = null; setStream(null);
    sessionRef.current = null; setSession(null); setPaused(false); setPauseReason(""); setPreflight(null); preflightRef.current = null; chunksRef.current = [];
    if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
  }, []);

  useEffect(() => {
    const current = sessionRef.current;
    const camera = stream;
    if (!current || !camera) return;
    closingRef.current = false;
    let cancelled = false;
    let recorder: MediaRecorder | null = null;
    if (typeof MediaRecorder !== "undefined") {
      const mimeType = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm", "video/mp4"].find((type) => MediaRecorder.isTypeSupported(type)) || "";
      recorder = new MediaRecorder(camera, mimeType ? { mimeType, videoBitsPerSecond: 600_000 } : undefined);
      recorderRef.current = recorder;
      let chunkStarted = new Date();
      recorder.ondataavailable = (event) => {
        const endedAt = new Date();
        if (event.data.size) {
          const chunk = { blob: event.data, startedAt: chunkStarted, endedAt };
          const buffered = [...chunksRef.current, chunk].slice(-3);
          chunksRef.current = buffered;
          if (evidenceReasonRef.current) {
            const reason = evidenceReasonRef.current; evidenceReasonRef.current = "";
            void uploadEvidence(reason, buffered); chunksRef.current = [chunk];
          }
        }
        chunkStarted = endedAt;
      };
      if (current.policy.event_recording) recorder.start(10_000);
    }

    const visibility = () => { if (document.hidden) void report("viewer_hidden", "critical", { visibility: document.visibilityState }); };
    const fullscreen = () => { if (!closingRef.current && current.policy.fullscreen_required && !document.fullscreenElement) void report("fullscreen_exited", "critical", { visibility: document.visibilityState }); };
    const trackStopped = () => { if (!closingRef.current) void report("camera_stopped", "critical"); };
    camera.getVideoTracks().forEach((track) => { track.addEventListener("ended", trackStopped); track.addEventListener("mute", trackStopped); });
    const deviceChange = async () => { const inventory = await mediaInventory(); if (inventory.rawDigest !== inventoryRef.current) void report("external_media_device", "critical", { previous_inventory: inventoryRef.current, current_inventory: inventory.public }); };
    document.addEventListener("visibilitychange", visibility);
    document.addEventListener("fullscreenchange", fullscreen);
    navigator.mediaDevices.addEventListener("devicechange", deviceChange);

    let failedHeartbeats = 0;
    const heartbeat = window.setInterval(async () => {
      try {
        const result = await request(`/api/v1/phase4/remote-security/sessions/${current.id}/heartbeat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ posture: await posture(false) }) });
        failedHeartbeats = 0; if (result.session) updateSession(result.session);
      } catch {
        failedHeartbeats += 1;
        if (failedHeartbeats >= 2) { setPaused(true); setPauseReason("heartbeat_lost"); evidenceReasonRef.current ||= "heartbeat_lost"; }
      }
    }, Math.max(current.policy.heartbeat_seconds, 5) * 1000);

    const analysisVideo = document.createElement("video"); analysisVideo.muted = true; analysisVideo.playsInline = true; analysisVideo.srcObject = camera; void analysisVideo.play();
    const canvas = document.createElement("canvas"); canvas.width = 64; canvas.height = 48;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    const Detector = (window as typeof window & { FaceDetector?: FaceDetectorConstructor }).FaceDetector;
    const detector = Detector ? new Detector({ fastMode: true, maxDetectedFaces: 3 }) : null;
    let darkFrames = 0; let noFaceChecks = 0; let multipleFaceChecks = 0; let noFaceWarned = false;
    const analyze = window.setInterval(async () => {
      if (cancelled || analysisVideo.readyState < 2 || !context || paused) return;
      context.drawImage(analysisVideo, 0, 0, canvas.width, canvas.height);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let luminance = 0; for (let index = 0; index < pixels.length; index += 4) luminance += (pixels[index] + pixels[index + 1] + pixels[index + 2]) / 3;
      luminance /= pixels.length / 4;
      darkFrames = luminance < 12 ? darkFrames + 1 : 0;
      if (darkFrames >= 3) { darkFrames = 0; void report("camera_obstructed", "critical", { mean_luminance: Math.round(luminance) }); return; }
      if (!detector) return;
      try {
        const faces = await detector.detect(analysisVideo);
        noFaceChecks = faces.length === 0 ? noFaceChecks + 1 : 0;
        multipleFaceChecks = faces.length > 1 ? multipleFaceChecks + 1 : 0;
        if (faces.length > 0) noFaceWarned = false;
        const warnChecks = Math.max(1, Math.floor(current.policy.no_face_seconds / 10));
        const pauseChecks = Math.max(2, Math.ceil(current.policy.no_face_seconds / 5));
        if (noFaceChecks >= warnChecks && !noFaceWarned) { noFaceWarned = true; void report("face_absent_warning", "medium", { duration_seconds: noFaceChecks * 5 }); }
        if (noFaceChecks >= pauseChecks) { noFaceChecks = 0; void report("face_absent", "critical", { duration_seconds: current.policy.no_face_seconds }); }
        if (multipleFaceChecks >= 2) { multipleFaceChecks = 0; void report("multiple_faces", "critical", { sustained_seconds: 10 }); }
      } catch { /* Native face detection is a progressive signal; camera continuity still applies. */ }
    }, 5000);

    return () => {
      cancelled = true; window.clearInterval(heartbeat); window.clearInterval(analyze);
      document.removeEventListener("visibilitychange", visibility); document.removeEventListener("fullscreenchange", fullscreen); navigator.mediaDevices.removeEventListener("devicechange", deviceChange);
      camera.getVideoTracks().forEach((track) => { track.removeEventListener("ended", trackStopped); track.removeEventListener("mute", trackStopped); });
      analysisVideo.srcObject = null;
      if (recorder && recorder.state !== "inactive") recorder.stop();
    };
  }, [paused, posture, report, session?.id, stream, updateSession, uploadEvidence]);

  return { policy, preflight, stream, session, paused, pauseReason, checking, error, needsReauthentication, prepare, start, resume, finish, report, pause };
}

export function CameraPreview({ stream, compact = false }: { stream: MediaStream | null; compact?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => { if (ref.current) { ref.current.srcObject = stream; void ref.current.play().catch(() => undefined); } }, [stream]);
  return <div className={compact ? "camera-preview compact" : "camera-preview"}>{stream ? <video ref={ref} muted playsInline aria-label="Evaluator webcam preview" /> : <Camera />}<span>{stream && <i />}{stream ? "Camera active" : "Camera required"}</span></div>;
}

export function SecurePreflightDialog({ script, controller, onStart, onCancel }: { script: string; controller: ReturnType<typeof useSecureEvaluationSession>; onStart: (consent: boolean) => Promise<void>; onCancel: () => void }) {
  const [consent, setConsent] = useState(false);
  const blockedByScreen = Boolean(controller.policy?.single_screen_required && controller.preflight?.screen_count && controller.preflight.screen_count > 1);
  return <div className="modal-backdrop"><div className="modal-panel secure-preflight" role="dialog" aria-modal="true" aria-labelledby="secure-preflight-title"><header className="modal-header"><div><h2 id="secure-preflight-title">Secure evaluation check</h2><p>{script} · camera-monitored valuation session</p></div><button className="icon-button" title="Cancel" onClick={onCancel}><X /></button></header><div className="preflight-body"><CameraPreview stream={controller.stream}/><div className="preflight-checks"><div className={controller.preflight?.camera_ready ? "ready" : "pending"}>{controller.preflight?.camera_ready ? <Check /> : <Camera />}<span>Webcam</span><strong>{controller.preflight?.camera_ready ? "Ready" : "Required"}</strong></div><div className={blockedByScreen ? "blocked" : controller.preflight ? "ready" : "pending"}><Monitor /><span>Displays</span><strong>{controller.preflight?.screen_count === null ? "Browser-limited" : controller.preflight ? `${controller.preflight.screen_count} connected` : "Checking"}</strong></div><div className={controller.preflight ? "ready" : "pending"}><Video /><span>Evidence</span><strong>{controller.policy?.event_recording === false ? "Events only" : "Encrypted clips"}</strong></div><div className="pending"><LockKeyhole /><span>Fullscreen</span><strong>Enabled on start</strong></div></div><label className="security-consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)}/><span>I understand that security events may preserve short encrypted webcam clips for authorized human review.</span></label>{controller.error && <div className="form-error" role="alert">{controller.error}</div>}</div><footer className="modal-footer"><button className="secondary-button" onClick={controller.prepare} disabled={controller.checking}>{controller.checking ? <RefreshCw className="spin"/> : <Camera />}{controller.preflight ? "Run checks again" : "Run security checks"}</button><button className="primary-button" disabled={!controller.preflight?.camera_ready || !consent || blockedByScreen || controller.checking} onClick={() => onStart(consent)}><ShieldAlert />Begin secure evaluation</button></footer></div></div>;
}

export function SecurityPauseOverlay({ controller, onClose }: { controller: ReturnType<typeof useSecureEvaluationSession>; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const reason = controller.pauseReason.replaceAll("_", " ");
  return <div className="security-pause" role="alertdialog" aria-modal="true"><div><ShieldAlert /><h2>Evaluation paused</h2><p>{reason.charAt(0).toUpperCase() + reason.slice(1)} was detected. The script and marking controls remain locked until security checks pass.</p><CameraPreview stream={controller.stream} compact/>{controller.needsReauthentication && <label><span>Password</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>}{controller.error && <div className="viewer-error-inline">{controller.error}</div>}<div className="pause-actions"><button className="secondary-button" onClick={onClose}>Close script</button><button className="primary-button" disabled={controller.checking || (controller.needsReauthentication && !password)} onClick={() => controller.resume(password)}><LockKeyhole />Run checks and resume</button></div></div></div>;
}
