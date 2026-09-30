"use client";

import { Camera, Check, LockKeyhole, Monitor, RefreshCw, ShieldAlert, Video, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { csrfFetch, SESSION_EXPIRED_EVENT } from "@/lib/api";
import { safeDigest, safeRandomUUID } from "@/lib/crypto-compat";

type Policy = { identity_verification_required?: boolean; strict_mode?: boolean; camera_required: boolean; fullscreen_required: boolean; single_screen_required: boolean; mobile_allowed?: boolean; event_recording: boolean; pause_on_violation?: boolean; require_resume_step_up?: boolean; allow_clipboard?: boolean; allow_download?: boolean; allow_print?: boolean; session_timeout_minutes?: number; heartbeat_seconds: number; no_face_seconds: number; retention_days: number };
type Session = { id: string; assignment_id: string; status: string; pause_reason: string; violation_count: number; policy: Policy; version: number };
type Preflight = { camera_ready: boolean; face_ready: boolean; headphones_detected: boolean; fullscreen_active: boolean; screen_count: number | null; screen_check_supported: boolean; video_inputs: number; audio_inputs: number; mobile?: boolean; inventory_digest: string; preflight_token: string };
type Inventory = { video_inputs: number; audio_inputs: number; audio_outputs: number; headphones_detected: boolean; headphone_devices: number; mobile: boolean; digest: string };
type BufferedChunk = { blob: Blob; startedAt: Date; endedAt: Date };
type FacePayload = { image_base64: string; liveness_passed: boolean; face_count: number; quality: Record<string, unknown>; model_version: string; device_fingerprint: string };
type FaceDetectorLike = { detect(source: CanvasImageSource): Promise<unknown[]> };
type FaceDetectorConstructor = new (options?: { fastMode?: boolean; maxDetectedFaces?: number }) => FaceDetectorLike;
type MonitoringStatus = { camera: "ok" | "error" | "required"; face: "detected" | "not_detected"; multipleFaces: "none" | "detected"; mobile: "not_detected" | "detected" };
const modelVersion = "opencv-sface-v1";
const identityPauseReasons = new Set(["identity_mismatch", "identity_verification_failed", "face_absent", "multiple_faces", "camera_obstructed", "camera_stopped"]);

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
  return safeDigest(value);
}

async function mediaInventory(): Promise<{ public: Inventory; rawDigest: string }> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const headphonePattern = /head(?:phone|set)s?|ear(?:phone|bud)s?|airpods?|galaxy buds|pixel buds|hands[- ]?free/i;
  const headphoneDevices = devices.filter((item) => ["audioinput", "audiooutput"].includes(item.kind) && headphonePattern.test(item.label || ""));
  const normalized = devices.map((item) => `${item.kind}:${item.deviceId}:${item.groupId}:${headphonePattern.test(item.label || "")}`).sort().join("|");
  const rawDigest = await digest(normalized);
  return {
    rawDigest,
    public: {
      video_inputs: devices.filter((item) => item.kind === "videoinput").length,
      audio_inputs: devices.filter((item) => item.kind === "audioinput").length,
      audio_outputs: devices.filter((item) => item.kind === "audiooutput").length,
      headphones_detected: headphoneDevices.length > 0,
      headphone_devices: headphoneDevices.length,
      mobile: /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent),
      digest: rawDigest,
    },
  };
}

async function unlockAudioDeviceLabels() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputIds = devices
    .filter((item) => item.kind === "audioinput" && item.deviceId && !["default", "communications"].includes(item.deviceId))
    .map((item) => item.deviceId);
  const attempts: MediaStreamConstraints[] = [
    { video: false, audio: true },
    ...inputIds.map((deviceId) => ({ video: false, audio: { deviceId: { exact: deviceId } } })),
  ];
  for (const constraints of attempts) {
    try {
      const probe = await navigator.mediaDevices.getUserMedia(constraints);
      probe.getTracks().forEach((track) => track.stop());
      return true;
    } catch { /* Try the next microphone. Camera evaluation must remain usable. */ }
  }
  return false;
}

async function availableScreens() {
  const managedWindow = window as typeof window & { getScreenDetails?: () => Promise<{ screens: unknown[] }> };
  if (!managedWindow.getScreenDetails) return { supported: false, count: null as number | null };
  try {
    const details = await Promise.race([
      managedWindow.getScreenDetails(),
      new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 5000)),
    ]);
    if (!details) return { supported: false, count: null as number | null };
    return { supported: true, count: details.screens.length };
  } catch {
    return { supported: true, count: null as number | null };
  }
}

function cameraIsActive(stream: MediaStream | null) {
  return Boolean(stream?.getVideoTracks().some((track) => track.readyState === "live" && track.enabled && !track.muted));
}

async function cameraFrameCheck(stream: MediaStream) {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  await video.play();
  if (video.readyState < 2) throw new Error("Camera preview is not ready. Enable the camera and try again.");
  await new Promise((resolve) => window.setTimeout(resolve, 250));
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 48;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Camera preview could not be inspected.");
  const snapshot = () => {
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return context.getImageData(0, 0, canvas.width, canvas.height).data;
  };
  const first = snapshot();
  await new Promise((resolve) => window.setTimeout(resolve, 650));
  const second = snapshot();
  const pixels = second;
  if (frameLooksObstructed(pixels) || frameDelta(first, second) < 0.8) return "The camera appears covered, disabled, or frozen. Enable the camera, keep the lens visible, and move slightly.";
  const Detector = (window as typeof window & { FaceDetector?: FaceDetectorConstructor }).FaceDetector;
  if (Detector) {
    const faces = await new Detector({ fastMode: true, maxDetectedFaces: 2 }).detect(video);
    video.srcObject = null;
    if (faces.length !== 1) return "A clear single face is required. Keep your full face visible to the camera.";
  }
  video.srcObject = null;
  return "";
}

function frameDelta(left: Uint8ClampedArray, right: Uint8ClampedArray) {
  let total = 0;
  const step = 16;
  for (let index = 0; index < Math.min(left.length, right.length); index += step) total += Math.abs(left[index] - right[index]);
  return total / Math.max(1, Math.min(left.length, right.length) / step);
}

function frameLooksObstructed(data: Uint8ClampedArray) {
  let total = 0;
  let redTotal = 0;
  let greenTotal = 0;
  let blueTotal = 0;
  let squaredTotal = 0;
  for (let index = 0; index < data.length; index += 4) {
    const red = data[index];
    const green = data[index + 1];
    const blue = data[index + 2];
    const value = (red + green + blue) / 3;
    total += value;
    squaredTotal += value * value;
    redTotal += red;
    greenTotal += green;
    blueTotal += blue;
  }
  const count = data.length / 4;
  const mean = total / count;
  const variance = squaredTotal / count - mean * mean;
  const redDominance = redTotal / count - (greenTotal + blueTotal) / (count * 2);
  return mean < 28 || (variance < 80 && redDominance > 45 && greenTotal / count < 100 && blueTotal / count < 100);
}

function qualityFromFrame(data: Uint8ClampedArray, delta: number) {
  let luminance = 0;
  for (let index = 0; index < data.length; index += 4) luminance += (data[index] + data[index + 1] + data[index + 2]) / 3;
  luminance /= data.length / 4;
  const lightScore = Math.max(0, Math.min(1, 1 - Math.abs(luminance - 128) / 128));
  const motionScore = Math.max(0, Math.min(1, delta / 18));
  const score = Math.max(0, Math.min(0.99, lightScore * 0.68 + motionScore * 0.32));
  return { score: Number(score.toFixed(2)), luminance: Math.round(luminance), frame_delta: Number(delta.toFixed(2)) };
}

async function captureIdentityPayload(stream: MediaStream, deviceSeed = "", trigger = "periodic"): Promise<FacePayload> {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  await video.play();
  if (video.readyState < 2) await new Promise((resolve) => window.setTimeout(resolve, 300));
  if (video.readyState < 2) throw new Error("Camera preview is not ready for identity verification");
  const canvas = document.createElement("canvas");
  canvas.width = 640; canvas.height = 480;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Camera capture is unavailable");
  const snapshot = async () => {
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return context.getImageData(0, 0, canvas.width, canvas.height).data;
  };
  const first = await snapshot();
  await new Promise((resolve) => window.setTimeout(resolve, 650));
  const second = await snapshot();
  const delta = frameDelta(first, second);
  const quality = qualityFromFrame(second, delta);
  const Detector = (window as typeof window & { FaceDetector?: FaceDetectorConstructor }).FaceDetector;
  let faceCount = 1;
  let faceSupported = false;
  if (Detector) {
    faceSupported = true;
    try { faceCount = (await new Detector({ fastMode: true, maxDetectedFaces: 2 }).detect(video)).length; } catch { faceCount = 1; }
  }
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  const imageBase64 = canvas.toDataURL("image/jpeg", 0.88);
  video.srcObject = null;
  return {
    image_base64: imageBase64,
    liveness_passed: delta > 0.8 && faceCount === 1,
    face_count: faceCount,
    quality: { ...quality, face_detection_supported: faceSupported, source: "secure_evaluation_session", trigger },
    model_version: modelVersion,
    device_fingerprint: await digest([navigator.userAgent, screen.width, screen.height, Intl.DateTimeFormat().resolvedOptions().timeZone, deviceSeed].join("|")),
  };
}

function capturePresenceFrame(video: HTMLVideoElement) {
  const canvas = document.createElement("canvas");
  canvas.width = 640;
  canvas.height = 480;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Camera monitoring is unavailable");
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.82);
}

async function validateCameraFrame(stream: MediaStream, assignmentId: string) {
  if (!cameraIsActive(stream)) throw new Error("Turn on the webcam before opening the paper");
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  try {
    await video.play();
    if (video.readyState < 2) await new Promise((resolve) => window.setTimeout(resolve, 300));
    if (video.readyState < 2) throw new Error("Camera preview is not ready");
    const result = await request("/api/v1/phase4/remote-security/camera-preflight", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assignment_id: assignmentId, image_base64: capturePresenceFrame(video) }),
    }) as { camera_ready: boolean; face_ready: boolean; face_count: number; face_aligned?: boolean; phone_call_suspected?: boolean; posture_details?: Record<string, unknown>; preflight_token: string };
    if (result.face_aligned === false) throw new Error("Face the webcam directly. Side angle or tilted face is not allowed.");
    return result;
  } finally {
    video.srcObject = null;
  }
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
  const [monitoringStatus, setMonitoringStatus] = useState<MonitoringStatus>({ camera: "required", face: "not_detected", multipleFaces: "none", mobile: "not_detected" });
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
  const cameraRecheckInFlightRef = useRef(false);
  const assignmentRef = useRef("");

  const updateSession = useCallback((next: Session) => {
    sessionRef.current = next;
    setSession((current) => current && current.id === next.id && current.status === next.status && current.pause_reason === next.pause_reason && current.violation_count === next.violation_count && current.version === next.version ? current : next);
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

  const pause = useCallback((category: string) => { setPaused(true); setPauseReason(category); setNeedsReauthentication(true); setChecking(true); evidenceReasonRef.current ||= category; }, []);

  const forceSecurityLogout = useCallback(async (reason = "multiple_faces") => {
    closingRef.current = true;
    sessionStorage.removeItem("admiezo-secure-evaluation-id");
    sessionStorage.removeItem("admiezo-secure-session-fingerprint");
    recorderRef.current?.stop(); recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop()); streamRef.current = null; setStream(null);
    sessionRef.current = null; setSession(null); setPaused(false); setPauseReason(""); setPreflight(null); preflightRef.current = null; chunksRef.current = [];
    if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
    const notice = reason === "phone_detected"
      ? "Mobile phone detected. Evaluation access was terminated. Sign in again."
      : reason === "headphones_detected"
        ? "Headphones or a headset were detected. Evaluation access was terminated. Sign in again."
        : reason === "external_media_device"
          ? "A media device was connected or changed. Evaluation access was terminated. Sign in again."
        : reason === "identity_mismatch"
          ? "Your live face did not match the enrolled evaluator. Evaluation access was terminated. Sign in again."
          : "Multiple people were detected. Evaluation access was terminated. Sign in again.";
    window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT, { detail: { notice, loginRole: "evaluator" } }));
  }, []);

  const report = useCallback(async (category: string, severity: "low" | "medium" | "high" | "critical", details: Record<string, unknown> = {}) => {
    const current = sessionRef.current;
    if (!current || closingRef.current || eventInFlightRef.current.has(category)) return;
    const shouldPause = severity === "critical" || ["viewer_hidden", "fullscreen_exited", "camera_stopped", "external_media_device", "multiple_screens", "multiple_faces", "camera_obstructed", "identity_mismatch", "face_misaligned"].includes(category);
    if (shouldPause) pause(category);
    eventInFlightRef.current.add(category);
    try {
      const result = await request("/api/v1/phase4/remote-security/events", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assignment_id: current.assignment_id, secure_session_id: current.id, category, severity, device_fingerprint: preflightRef.current?.inventory_digest || "0".repeat(64), session_fingerprint: sessionStorage.getItem("admiezo-secure-session-fingerprint") || "0".repeat(64), details }) });
      if (result.session) {
        updateSession(result.session);
        if (result.session.status === "abandoned") { await forceSecurityLogout(category); return; }
      }
      if (result.action === "pause") { setPaused(true); setPauseReason(category); }
    } catch {
      if (shouldPause) setError("The session remains paused until the security event is acknowledged by the server.");
    } finally {
      if (shouldPause) setChecking(false);
      window.setTimeout(() => eventInFlightRef.current.delete(category), 3000);
    }
  }, [forceSecurityLogout, pause, updateSession]);

  const verifyLiveIdentity = useCallback(async (assignmentId: string, trigger: string) => {
    const camera = streamRef.current;
    if (!camera || !cameraIsActive(camera)) throw new Error("Camera is required for evaluator identity verification");
    const payload = await captureIdentityPayload(camera, preflightRef.current?.inventory_digest || "", trigger);
    let body: Record<string, unknown>;
    try {
      body = await request("/api/v1/evaluator-management/face/verify-access", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assignment_id: assignmentId, ...payload }) });
    } catch (reason) {
      if (reason instanceof ApiError && reason.message === "face_not_enrolled" && payload.liveness_passed && payload.face_count === 1) return { access_granted: true, live_face_only: true };
      throw reason;
    }
    if (!body.access_granted) throw new Error(String(body.failure_reason || "Evaluator face does not match the enrolled template"));
    return body;
  }, []);

  const prepare = useCallback(async (assignmentId?: string) => {
    setChecking(true); setError(""); setPreflight(null);
    try {
      if (!assignmentId) throw new Error("Select an assignment before running security checks");
      assignmentRef.current = assignmentId;
      const currentPolicy = await request("/api/v1/phase4/remote-security/policy") as Policy;
      setPolicy(currentPolicy);
      const videoConstraints = { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } };
      const camera = await navigator.mediaDevices.getUserMedia({ video: videoConstraints, audio: false });
      await unlockAudioDeviceLabels();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = camera; setStream(camera); setMonitoringStatus((current) => ({ ...current, camera: "ok" }));
      const cameraMessage = await cameraFrameCheck(camera);
      const [cameraCheck, inventory, screens] = await Promise.all([
        validateCameraFrame(camera, assignmentId),
        mediaInventory(),
        availableScreens(),
      ]);
      inventoryRef.current = inventory.rawDigest;
      let faceReady = currentPolicy.identity_verification_required === false || cameraCheck.face_ready;
      if (!cameraMessage && faceReady && assignmentId && currentPolicy.identity_verification_required !== false) {
        try { await verifyLiveIdentity(assignmentId, "preflight"); }
        catch (reason) { faceReady = false; setError(reason instanceof Error ? reason.message : "A clear single face is required before secure evaluation can start."); }
      }
      const result = { camera_ready: cameraIsActive(camera) && cameraCheck.camera_ready && !cameraMessage, face_ready: faceReady, headphones_detected: inventory.public.headphones_detected, fullscreen_active: false, screen_count: screens.count, screen_check_supported: screens.supported, video_inputs: inventory.public.video_inputs, audio_inputs: inventory.public.audio_inputs, mobile: inventory.public.mobile, inventory_digest: inventory.public.digest, preflight_token: cameraCheck.preflight_token };
      preflightRef.current = result; setPreflight(result);
      if (cameraMessage) { setMonitoringStatus((current) => ({ ...current, camera: "error" })); setError(cameraMessage); }
      if (result.headphones_detected) setError("Disconnect headphones, headsets, earbuds, and AirPods before starting evaluation.");
      if (currentPolicy.single_screen_required && screens.count !== null && screens.count > 1) setError("Disconnect additional displays before starting evaluation.");
      if (!currentPolicy.mobile_allowed && inventory.public.mobile) setError("Mobile devices are not allowed for this evaluation.");
    } catch (reason) {
      const message = reason instanceof Error && reason.name === "NotAllowedError"
        ? "Camera permission is required for secure evaluation. Allow camera access and run the checks again."
        : reason instanceof Error && reason.name === "NotFoundError"
          ? "No working webcam was found. Connect or enable a camera, then run the checks again."
          : reason instanceof Error ? reason.message : "Security checks could not be completed";
      setMonitoringStatus((current) => ({ ...current, camera: "error" }));
      setError(message);
    } finally { setChecking(false); }
  }, [verifyLiveIdentity]);

  const recheckCamera = useCallback(async () => {
    if (cameraRecheckInFlightRef.current) return false;
    const camera = streamRef.current;
    if (!camera || !cameraIsActive(camera)) {
      setError("Open the webcam shutter and keep your face clearly visible");
      return false;
    }
    cameraRecheckInFlightRef.current = true;
    try {
      const [cameraCheck, inventory, screens] = await Promise.all([
        validateCameraFrame(camera, assignmentRef.current),
        mediaInventory(),
        availableScreens(),
      ]);
      inventoryRef.current = inventory.rawDigest;
      const result = { camera_ready: cameraCheck.camera_ready, face_ready: policy?.identity_verification_required === false || cameraCheck.face_ready, headphones_detected: inventory.public.headphones_detected, fullscreen_active: false, screen_count: screens.count, screen_check_supported: screens.supported, video_inputs: inventory.public.video_inputs, audio_inputs: inventory.public.audio_inputs, mobile: inventory.public.mobile, inventory_digest: inventory.public.digest, preflight_token: cameraCheck.preflight_token };
      preflightRef.current = result;
      setPreflight(result);
      if (result.headphones_detected) {
        setError("Disconnect headphones, headsets, earbuds, and AirPods before starting evaluation.");
        return false;
      }
      if (!policy?.mobile_allowed && result.mobile) {
        setError("Mobile devices are not allowed for this evaluation.");
        return false;
      }
      if (policy?.single_screen_required && screens.count !== null && screens.count > 1) {
        setError("Disconnect additional displays before starting evaluation.");
        return false;
      }
      setMonitoringStatus((current) => ({ ...current, camera: "ok" }));
      setError("");
      return result.camera_ready && result.face_ready;
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Camera security check failed";
      setMonitoringStatus((current) => ({ ...current, camera: "error" }));
      setError(message);
      return false;
    } finally {
      cameraRecheckInFlightRef.current = false;
    }
  }, [policy]);

  const start = useCallback(async (assignmentId: string, consent: boolean) => {
    if (!policy || !preflightRef.current || !streamRef.current) throw new Error("Run the security checks first");
    if (policy.camera_required && !cameraIsActive(streamRef.current)) {
      setError("A live webcam is required before secure evaluation can start.");
      throw new Error("A live webcam is required before secure evaluation can start.");
    }
    setChecking(true); setError(""); closingRef.current = false;
    try {
      const cameraMessage = await cameraFrameCheck(streamRef.current);
      if (cameraMessage) {
        setMonitoringStatus((current) => ({ ...current, camera: "error" }));
        setPreflight((current) => current ? { ...current, camera_ready: false } : current);
        preflightRef.current = { ...preflightRef.current, camera_ready: false };
        throw new Error(cameraMessage);
      }
      const verifiedFrame = await validateCameraFrame(streamRef.current, assignmentId);
      if (policy.identity_verification_required !== false) await verifyLiveIdentity(assignmentId, "secure_start");
      if (policy.fullscreen_required && !document.fullscreenElement) await document.documentElement.requestFullscreen();
      const screens = await availableScreens();
      const inventory = await mediaInventory();
      if (policy.camera_required && inventory.public.video_inputs < 1) {
        throw new Error("A connected webcam is required before secure evaluation can start.");
      }
      if (inventory.public.headphones_detected) throw new Error("Disconnect headphones, headsets, earbuds, and AirPods before starting evaluation.");
      if (!policy.mobile_allowed && inventory.public.mobile) throw new Error("Mobile devices are not allowed for this evaluation.");
      const currentPreflight = { ...preflightRef.current, camera_ready: cameraIsActive(streamRef.current) && verifiedFrame.camera_ready, face_ready: policy.identity_verification_required === false || verifiedFrame.face_ready, headphones_detected: inventory.public.headphones_detected, fullscreen_active: Boolean(document.fullscreenElement), screen_count: screens.count, mobile: inventory.public.mobile, preflight_token: verifiedFrame.preflight_token };
      const sessionFingerprint = await digest(`${safeRandomUUID()}:${Date.now()}:${assignmentId}`);
      const deviceFingerprint = await digest([navigator.userAgent, screen.width, screen.height, Intl.DateTimeFormat().resolvedOptions().timeZone, inventoryRef.current].join("|"));
      const created = await request("/api/v1/phase4/remote-security/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assignment_id: assignmentId, session_fingerprint: sessionFingerprint, device_fingerprint: deviceFingerprint, consent, preflight_token: verifiedFrame.preflight_token, preflight: currentPreflight, device_inventory: inventory.public }) }) as Session;
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
  }, [policy, updateSession, verifyLiveIdentity]);

  const resume = useCallback(async (password = "") => {
    const current = sessionRef.current;
    if (!current) return false;
    setChecking(true); setError("");
    try {
      if (!cameraIsActive(streamRef.current)) {
        const replacement = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        streamRef.current = replacement; setStream(replacement);
      }
      const verifiedFrame = await validateCameraFrame(streamRef.current as MediaStream, current.assignment_id);
      if (current.policy.fullscreen_required && !document.fullscreenElement) await document.documentElement.requestFullscreen();
      if (current.policy.identity_verification_required !== false && identityPauseReasons.has(current.pause_reason)) {
        try { await verifyLiveIdentity(current.assignment_id, "resume"); }
        catch (reason) {
          const message = reason instanceof Error ? reason.message : "Evaluator identity could not be verified";
          void report("identity_mismatch", "critical", { trigger: "resume", reason: message });
          throw new Error(message);
        }
      }
      if (password) await request("/api/v1/auth/step-up", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ password, code: "" }) });
      const next = await request(`/api/v1/phase4/remote-security/sessions/${current.id}/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ posture: await posture(false), preflight_token: verifiedFrame.preflight_token }) }) as Session;
      updateSession(next); setNeedsReauthentication(false); return true;
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 428) setNeedsReauthentication(true);
      setError(reason instanceof Error ? reason.message : "Secure session could not resume"); return false;
    } finally { setChecking(false); }
  }, [posture, report, updateSession, verifyLiveIdentity]);

  const finish = useCallback(async (completed: boolean) => {
    const current = sessionRef.current;
    closingRef.current = true;
    try {
      if (current) await request(`/api/v1/phase4/remote-security/sessions/${current.id}/finish`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ completed }) });
    } catch { /* Closing the local viewer must continue even if the network is gone. */ }
    sessionStorage.removeItem("admiezo-secure-evaluation-id");
    sessionStorage.removeItem("admiezo-secure-session-fingerprint");
    recorderRef.current?.stop(); recorderRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop()); streamRef.current = null; setStream(null); setMonitoringStatus({ camera: "required", face: "not_detected", multipleFaces: "none", mobile: "not_detected" });
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
      const mimeType = ["video/webm;codecs=vp8", "video/webm", "video/mp4", "video/webm;codecs=vp9"].find((type) => MediaRecorder.isTypeSupported(type)) || "";
      recorder = new MediaRecorder(camera, mimeType ? { mimeType, videoBitsPerSecond: 350_000 } : undefined);
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
    const trackStopped = () => {
      if (closingRef.current) return;
      setError("The camera appears covered, disabled, or frozen. Enable the camera, keep the lens visible, and move slightly.");
      setMonitoringStatus((current) => ({ ...current, camera: "error" }));
      setPreflight((current) => current ? { ...current, camera_ready: false } : current);
      preflightRef.current = preflightRef.current ? { ...preflightRef.current, camera_ready: false } : null;
      pause("camera_stopped");
      void report("camera_stopped", "critical");
    };
    camera.getVideoTracks().forEach((track) => { track.addEventListener("ended", trackStopped); track.addEventListener("mute", trackStopped); });
    const deviceChange = async () => {
      const inventory = await mediaInventory();
      if (inventory.public.headphones_detected) void report("headphones_detected", "critical", { headphone_devices: inventory.public.headphone_devices });
      else if (inventory.rawDigest !== inventoryRef.current) void report("external_media_device", "critical", { previous_inventory: inventoryRef.current, current_inventory: inventory.public, connected_or_changed: true });
    };
    document.addEventListener("visibilitychange", visibility);
    document.addEventListener("fullscreenchange", fullscreen);
    navigator.mediaDevices.addEventListener("devicechange", deviceChange);

    const cameraWatch = window.setInterval(() => { if (!closingRef.current && !paused && !cameraIsActive(camera)) trackStopped(); }, 500);

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
    let obstructedFrames = 0; let noFaceChecks = 0; let faceMisalignedChecks = 0; let noFaceWarned = false; let identityCheckInFlight = false; let presenceCheckInFlight = false; let lastIdentityCheckAt = Date.now();
    const identityCheckIntervalMs = Math.max(30_000, current.policy.heartbeat_seconds * 3 * 1000);
    const verifySessionIdentity = async (trigger: string) => {
      if (identityCheckInFlight || closingRef.current || paused) return;
      identityCheckInFlight = true;
      try {
        await verifyLiveIdentity(current.assignment_id, trigger);
      } catch (reason) {
        const message = reason instanceof Error ? reason.message : "Evaluator identity could not be verified";
        const confirmedMismatch = reason instanceof ApiError && reason.status === 409 && message.toLowerCase().includes("face_mismatch");
        if (!confirmedMismatch) {
          setError("Live identity could not be verified. The paper is hidden until verification succeeds.");
          await report("identity_verification_failed", "critical", { trigger, reason: message });
          return;
        }
        pause("identity_mismatch");
        setError("Checking the enrolled evaluator face again...");
        await new Promise((resolve) => window.setTimeout(resolve, 350));
        try {
          await verifyLiveIdentity(current.assignment_id, `${trigger}_confirmation`);
          setPaused(false); setPauseReason(""); setNeedsReauthentication(false); setChecking(false); setError("");
        } catch (confirmationReason) {
          const confirmationMessage = confirmationReason instanceof Error ? confirmationReason.message : "Evaluator identity could not be verified";
          const secondMismatch = confirmationReason instanceof ApiError && confirmationReason.status === 409 && confirmationMessage.toLowerCase().includes("face_mismatch");
          if (secondMismatch) {
            setError("The live evaluator does not match the enrolled face. Access is being terminated.");
            await report("identity_mismatch", "critical", { trigger, reason: confirmationMessage, consecutive_checks: 2 });
          } else {
            setError("Live identity could not be reconfirmed. The paper remains hidden.");
            await report("identity_verification_failed", "critical", { trigger, reason: confirmationMessage });
          }
        }
      } finally {
        identityCheckInFlight = false;
      }
    };
    const analyze = window.setInterval(async () => {
      if (cancelled || analysisVideo.readyState < 2 || !context || paused) return;
      if (!cameraIsActive(camera)) { trackStopped(); return; }
      context.drawImage(analysisVideo, 0, 0, canvas.width, canvas.height);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let luminance = 0; for (let index = 0; index < pixels.length; index += 4) luminance += (pixels[index] + pixels[index + 1] + pixels[index + 2]) / 3;
      luminance /= pixels.length / 4;
      obstructedFrames = frameLooksObstructed(pixels) ? obstructedFrames + 1 : 0;
      if (obstructedFrames >= 2) { obstructedFrames = 0; setError("The camera appears covered, disabled, or frozen. Enable the camera, keep the lens visible, and move slightly."); setMonitoringStatus((current) => ({ ...current, camera: "error" })); pause("camera_obstructed"); void report("camera_obstructed", "critical", { mean_luminance: Math.round(luminance) }); return; }
      if (!presenceCheckInFlight) {
        presenceCheckInFlight = true;
        try {
          const result = await request(`/api/v1/phase4/remote-security/sessions/${current.id}/face-presence`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ image_base64: capturePresenceFrame(analysisVideo) }),
          }) as { face_count: number; terminated: boolean; termination_reason?: string; face_aligned?: boolean; phone_detected?: boolean; phone_call_suspected?: boolean; event_id?: string | null; session?: Session; posture_details?: Record<string, unknown> };
          noFaceChecks = result.face_count === 0 ? noFaceChecks + 1 : 0;
          const face = result.face_count > 0 ? "detected" : "not_detected";
          const multipleFaces = result.face_count > 1 ? "detected" : "none";
          const mobile = result.phone_detected || result.phone_call_suspected ? "detected" : "not_detected";
          setMonitoringStatus((status) => status.face === face && status.multipleFaces === multipleFaces && status.mobile === mobile ? status : { ...status, face, multipleFaces, mobile });
          if (result.terminated) { await forceSecurityLogout(result.termination_reason); return; }
          if (mobile === "detected") {
            noFaceChecks = 0; faceMisalignedChecks = 0; noFaceWarned = false;
          } else {
            if (result.face_count === 1 && result.face_aligned === false) { faceMisalignedChecks += 1; } else { faceMisalignedChecks = 0; }
            if (result.face_count === 1 && result.face_aligned === false && faceMisalignedChecks >= 1) { faceMisalignedChecks = 0; pause("face_misaligned"); void report("face_misaligned", "critical", result.posture_details || {}); return; }
            if (result.face_count > 0) noFaceWarned = false;
            const warnChecks = 1;
            const pauseChecks = 2;
            if (noFaceChecks >= warnChecks && !noFaceWarned) { noFaceWarned = true; void report("face_absent_warning", "medium", { duration_seconds: noFaceChecks }); }
            if (noFaceChecks >= pauseChecks) { noFaceChecks = 0; pause("face_absent"); void report("face_absent", "critical", { duration_seconds: pauseChecks }); }
          }
          if (result.event_id && mobile === "detected" && result.session?.status === "paused") {
            evidenceReasonRef.current ||= "phone_detected";
            setNeedsReauthentication(true);
            updateSession(result.session);
            return;
          }
        } catch (reason) {
          if (reason instanceof ApiError && reason.status === 409) return;
        } finally {
          presenceCheckInFlight = false;
        }
      }
      const now = Date.now();
      if (current.policy.identity_verification_required !== false && now - lastIdentityCheckAt >= identityCheckIntervalMs) {
        lastIdentityCheckAt = now;
        void verifySessionIdentity("periodic");
      }
    }, 1000);

    return () => {
      cancelled = true; window.clearInterval(cameraWatch); window.clearInterval(heartbeat); window.clearInterval(analyze);
      document.removeEventListener("visibilitychange", visibility); document.removeEventListener("fullscreenchange", fullscreen); navigator.mediaDevices.removeEventListener("devicechange", deviceChange);
      camera.getVideoTracks().forEach((track) => { track.removeEventListener("ended", trackStopped); track.removeEventListener("mute", trackStopped); });
      analysisVideo.srcObject = null;
      if (recorder && recorder.state !== "inactive") recorder.stop();
    };
  }, [forceSecurityLogout, paused, posture, report, session?.id, stream, updateSession, uploadEvidence, verifyLiveIdentity]);

  return { policy, preflight, stream, session, paused, pauseReason, checking, error, needsReauthentication, monitoringStatus, prepare, recheckCamera, start, resume, finish, report, pause };
}

export function CameraPreview({ stream, compact = false }: { stream: MediaStream | null; compact?: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => { if (ref.current) { ref.current.srcObject = stream; void ref.current.play().catch(() => undefined); } }, [stream]);
  return <div className={compact ? "camera-preview compact" : "camera-preview"}>{stream ? <video ref={ref} muted playsInline aria-label="Evaluator webcam preview" /> : <Camera />}<span>{stream && <i />}{stream ? "Camera active" : "Camera required"}</span></div>;
}

export function SecurePreflightDialog({ script, assignmentId, controller, onStart, onCancel }: { script: string; assignmentId: string; controller: ReturnType<typeof useSecureEvaluationSession>; onStart: (consent: boolean) => Promise<void>; onCancel: () => void }) {
  const [consent, setConsent] = useState(false);
  const recoveredCameraRef = useRef(false);
  const startingRef = useRef(false);
  const blockedByScreen = Boolean(controller.policy?.single_screen_required && controller.preflight?.screen_count && controller.preflight.screen_count > 1);
  const cameraReady = controller.preflight?.camera_ready === true && controller.monitoringStatus.camera === "ok";
  const faceReady = controller.preflight?.face_ready === true;
  const blockedByHeadphones = Boolean(controller.preflight?.headphones_detected);
  const cameraNeedsRecovery = Boolean(controller.stream && controller.error && /camera|webcam|face|shutter|visible|aligned/i.test(controller.error));

  useEffect(() => {
    if (!cameraNeedsRecovery) return;
    let disposed = false;
    const check = async () => {
      const ready = await controller.recheckCamera();
      if (!disposed && ready) recoveredCameraRef.current = true;
    };
    void check();
    const timer = window.setInterval(() => void check(), 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [cameraNeedsRecovery, controller.recheckCamera]);

  useEffect(() => {
    if (!recoveredCameraRef.current || startingRef.current || !consent || blockedByScreen || blockedByHeadphones || controller.checking || !controller.preflight?.camera_ready || !controller.preflight.face_ready) return;
    startingRef.current = true;
    void onStart(true).catch(() => { startingRef.current = false; });
  }, [blockedByHeadphones, blockedByScreen, consent, controller.checking, controller.preflight?.camera_ready, controller.preflight?.face_ready, onStart]);
  return <div className="modal-backdrop"><div className="modal-panel secure-preflight" role="dialog" aria-modal="true" aria-labelledby="secure-preflight-title"><header className="modal-header"><div><h2 id="secure-preflight-title">Secure evaluation check</h2><p>{script} · camera-monitored valuation session</p></div><button className="icon-button" title="Cancel" onClick={onCancel}><X /></button></header><div className="preflight-body"><CameraPreview stream={controller.stream}/><div className="preflight-checks"><div className={cameraReady ? "ready" : "pending"}>{cameraReady ? <Check /> : <Camera />}<span>Webcam</span><strong>{cameraReady ? "Ready" : "Required"}</strong></div><div className={faceReady ? "ready" : "pending"}><ShieldAlert /><span>Face</span><strong>{faceReady ? "Verified" : "Required"}</strong></div><div className={blockedByScreen ? "blocked" : controller.preflight ? "ready" : "pending"}><Monitor /><span>Displays</span><strong>{controller.preflight?.screen_count === null ? "Browser-limited" : controller.preflight ? `${controller.preflight.screen_count} connected` : "Checking"}</strong></div><div className={blockedByHeadphones ? "blocked" : controller.preflight ? "ready" : "pending"}><Video /><span>Headphones</span><strong>{blockedByHeadphones ? "Disconnect" : "None"}</strong></div><div className="pending"><LockKeyhole /><span>Fullscreen</span><strong>Enabled on start</strong></div></div><label className="security-consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)}/><span>I understand that security events may preserve short encrypted webcam clips for authorized human review.</span></label>{controller.error && <div className="form-error" role="alert">{controller.error}</div>}</div><footer className="modal-footer"><button className="secondary-button" onClick={() => controller.prepare(assignmentId)} disabled={controller.checking}>{controller.checking ? <RefreshCw className="spin"/> : <Camera />}{controller.preflight ? "Run checks again" : "Run security checks"}</button><button className="primary-button" disabled={!cameraReady || !faceReady || !consent || blockedByScreen || blockedByHeadphones || controller.checking} onClick={() => onStart(consent)}><ShieldAlert />Begin secure evaluation</button></footer></div></div>;
}

export function SecurityPauseOverlay({ controller, onClose }: { controller: ReturnType<typeof useSecureEvaluationSession>; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const reason = controller.pauseReason.replaceAll("_", " ");
  const passwordRequired = controller.session?.policy.require_resume_step_up !== false && (controller.session?.violation_count || 0) >= 3;
  return <div className="security-pause" role="alertdialog" aria-modal="true"><div><ShieldAlert /><h2>Evaluation paused</h2><p>{reason.charAt(0).toUpperCase() + reason.slice(1)} was detected. The script and marking controls remain locked until security checks pass.</p><CameraPreview stream={controller.stream} compact/>{passwordRequired && <label><span>Password</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>}{controller.error && <div className="viewer-error-inline">{controller.error}</div>}<div className="pause-actions"><button className="secondary-button" onClick={onClose}>Close script</button><button className="primary-button" disabled={controller.checking || (passwordRequired && !password)} onClick={() => controller.resume(passwordRequired ? password : "")}><LockKeyhole />Run checks and resume</button></div></div></div>;
}
