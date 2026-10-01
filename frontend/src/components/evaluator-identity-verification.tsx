// "use client";

// import { Camera, Check, RefreshCw, ScanFace, ShieldCheck, X } from "lucide-react";
// import { useCallback, useEffect, useRef, useState } from "react";
// import { csrfFetch } from "@/lib/api";
// import { safeDigest } from "@/lib/crypto-compat";

// type FaceStatus = { enrolled?: boolean; status?: string; evaluator_id?: string; evaluator_name?: string; evaluator_code?: string };
// type IdentityEvaluator = { id: string; display_name: string; evaluator_code?: string; is_system_ai?: boolean; face_status?: string; face_enrolled?: boolean };
// type IdentityAssignment = { id: string; script: string; paper?: string };
// type FacePayload = { image_base64: string; liveness_passed: boolean; face_count: number; quality: Record<string, unknown>; model_version: string; device_fingerprint: string };
// type FaceDetectorLike = { detect(source: CanvasImageSource): Promise<unknown[]> };
// type FaceDetectorConstructor = new (options?: { fastMode?: boolean; maxDetectedFaces?: number }) => FaceDetectorLike;
// type BlazeFacePrediction = { topLeft?: [number, number]; bottomRight?: [number, number]; landmarks?: Array<[number, number]> };
// type BlazeFaceModel = { estimateFaces(source: HTMLVideoElement, returnTensors?: false): Promise<BlazeFacePrediction[]> };

// const modelVersion = "opencv-sface-v1";
// let blazeFaceModelPromise: Promise<BlazeFaceModel | null> | null = null;

// function loadBlazeFaceShortRange() {
//   if (!blazeFaceModelPromise) {
//     blazeFaceModelPromise = (async () => {
//       const tf = await import("@tensorflow/tfjs-core");
//       await import("@tensorflow/tfjs-backend-webgl");
//       await tf.setBackend("webgl").catch(() => tf.setBackend("cpu"));
//       await tf.ready();
//       const blazeface = await import("@tensorflow-models/blazeface");
//       return await blazeface.load({ maxFaces: 2, scoreThreshold: 0.75 }) as BlazeFaceModel;
//     })().catch(() => null);
//   }
//   return blazeFaceModelPromise;
// }

// async function validateEnrollmentPose(video: HTMLVideoElement) {
//   const model = await loadBlazeFaceShortRange();
//   if (!model) return;
//   const faces = await model.estimateFaces(video, false);
//   if (faces.length === 0) throw new Error("Keep the evaluator face clearly visible.");
//   if (faces.length > 1) throw new Error("Only one evaluator face is allowed for enrollment.");
//   const face = faces[0];
//   const topLeft = face.topLeft;
//   const bottomRight = face.bottomRight;
//   if (!Array.isArray(topLeft) || !Array.isArray(bottomRight)) throw new Error("Face could not be measured. Look directly at the camera.");
//   const width = Number(bottomRight[0]) - Number(topLeft[0]);
//   const height = Number(bottomRight[1]) - Number(topLeft[1]);
//   const centerX = (Number(topLeft[0]) + width / 2) / video.videoWidth;
//   const centerY = (Number(topLeft[1]) + height / 2) / video.videoHeight;
//   const faceShare = width / video.videoWidth;
//   const faceHeightShare = height / video.videoHeight;
//   let eyeTilt = 0;
//   let noseOffset = 0;
//   const landmarks = face.landmarks || [];
//   if (landmarks.length >= 3) {
//     const [rightEye, leftEye, nose] = landmarks;
//     const eyeDistance = Math.max(Math.abs(leftEye[0] - rightEye[0]), 1);
//     eyeTilt = Math.abs(leftEye[1] - rightEye[1]) / eyeDistance;
//     noseOffset = Math.abs(nose[0] - ((leftEye[0] + rightEye[0]) / 2)) / eyeDistance;
//   }
//   if (centerX < 0.37 || centerX > 0.63 || centerY < 0.24 || centerY > 0.70 || faceShare < 0.16 || faceHeightShare < 0.20 || eyeTilt > 0.16 || noseOffset > 0.24) {
//     throw new Error("Face the camera directly and keep the full face centered for enrollment.");
//   }
// }

// async function request(path: string, options?: RequestInit) {
//   const response = await csrfFetch(path, options);
//   const body = await response.json().catch(() => ({}));
//   if (!response.ok) throw new Error(body.detail || "Identity verification failed");
//   return body;
// }

// async function digest(value: string | ArrayBuffer) {
//   return safeDigest(value);
// }

// function stopStream(stream: MediaStream | null) {
//   stream?.getTracks().forEach((track) => track.stop());
// }

// function cameraActive(stream: MediaStream | null) {
//   return Boolean(stream?.getVideoTracks().some((track) => track.readyState === "live"));
// }

// function qualityFromFrame(data: Uint8ClampedArray, delta: number) {
//   let luminance = 0;
//   for (let index = 0; index < data.length; index += 4) luminance += (data[index] + data[index + 1] + data[index + 2]) / 3;
//   luminance /= data.length / 4;
//   const lightScore = Math.max(0, Math.min(1, 1 - Math.abs(luminance - 128) / 128));
//   const motionScore = Math.max(0, Math.min(1, delta / 18));
//   const score = Math.max(0, Math.min(0.99, lightScore * 0.68 + motionScore * 0.32));
//   return { score: Number(score.toFixed(2)), luminance: Math.round(luminance), frame_delta: Number(delta.toFixed(2)) };
// }

// function frameDelta(left: Uint8ClampedArray, right: Uint8ClampedArray) {
//   let total = 0;
//   const step = 16;
//   for (let index = 0; index < Math.min(left.length, right.length); index += step) total += Math.abs(left[index] - right[index]);
//   return total / Math.max(1, Math.min(left.length, right.length) / step);
// }

// export function IdentityStatusBadge({ evaluator }: { evaluator: IdentityEvaluator }) {
//   if (evaluator.is_system_ai) return <span className="status-pill active">System managed</span>;
//   const status = evaluator.face_status || (evaluator.face_enrolled ? "active" : "not_enrolled");
//   return <span className={`status-pill ${status === "active" ? "active" : "attention"}`}>{status === "active" ? "Face enrolled" : "Face pending"}</span>;
// }

// export function IdentityVerificationModal({ mode, evaluator, assignment, onClose, onComplete }: { mode: "enroll" | "verify"; evaluator?: IdentityEvaluator; assignment?: IdentityAssignment; onClose: () => void; onComplete: (result: Record<string, unknown>) => void | Promise<void> }) {
//   const videoRef = useRef<HTMLVideoElement>(null);
//   const streamRef = useRef<MediaStream | null>(null);
//   const cameraRequest = useRef(0);
//   const [stream, setStream] = useState<MediaStream | null>(null);
//   const [selfStatus, setSelfStatus] = useState<FaceStatus | null>(null);
//   const [busy, setBusy] = useState(false);
//   const [error, setError] = useState("");
//   const [checks, setChecks] = useState({ camera: false, face: false, liveness: false, quality: false });
//   const [result, setResult] = useState<Record<string, unknown> | null>(null);

//   const startCamera = useCallback(async () => {
//     const requestId = ++cameraRequest.current;
//     setBusy(true); setError("");
//     try {
//       const camera = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false });
//       if (requestId !== cameraRequest.current) { stopStream(camera); return; }
//       stopStream(streamRef.current);
//       streamRef.current = camera;
//       setStream(camera);
//       setChecks((current) => ({ ...current, camera: cameraActive(camera) }));
//     } catch (reason) {
//       if (requestId !== cameraRequest.current) return;
//       setError(reason instanceof Error && reason.name === "NotAllowedError" ? "Camera permission is required." : reason instanceof Error ? reason.message : "Camera could not start.");
//     } finally { if (requestId === cameraRequest.current) setBusy(false); }
//   }, []);

//   useEffect(() => {
//     const timer = window.setTimeout(() => void startCamera(), 0);
//     return () => {
//       window.clearTimeout(timer);
//       cameraRequest.current += 1;
//       stopStream(streamRef.current);
//     };
//   }, [startCamera]);
//   useEffect(() => { if (videoRef.current) { videoRef.current.srcObject = stream; void videoRef.current.play().catch(() => undefined); } }, [stream]);
//   useEffect(() => {
//     if (mode !== "verify") return;
//     let cancelled = false;
//     void request("/api/v1/evaluator-management/face/status").then((body) => { if (!cancelled) setSelfStatus(body); }).catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "Face enrollment was not found."); });
//     return () => { cancelled = true; };
//   }, [mode]);

//   async function capturePayload(): Promise<FacePayload> {
//     const video = videoRef.current;
//     if (!video || !streamRef.current || video.readyState < 2) throw new Error("Camera preview is not ready.");
//     if (mode === "enroll") await validateEnrollmentPose(video);
//     const canvas = document.createElement("canvas");
//     canvas.width = 640; canvas.height = 480;
//     const context = canvas.getContext("2d", { willReadFrequently: true });
//     if (!context) throw new Error("Camera capture is unavailable.");
//     const snapshot = async () => {
//       context.drawImage(video, 0, 0, canvas.width, canvas.height);
//       return context.getImageData(0, 0, canvas.width, canvas.height).data;
//     };
//     const first = await snapshot();
//     await new Promise((resolve) => window.setTimeout(resolve, 650));
//     const second = await snapshot();
//     const delta = frameDelta(first, second);
//     const quality = qualityFromFrame(second, delta);
//     const Detector = (window as typeof window & { FaceDetector?: FaceDetectorConstructor }).FaceDetector;
//     let faceCount = 1;
//     let faceSupported = false;
//     if (Detector) {
//       faceSupported = true;
//       try { faceCount = (await new Detector({ fastMode: true, maxDetectedFaces: 2 }).detect(video)).length; } catch { faceCount = 1; }
//     }
//     const liveness = delta > 1.1 && faceCount === 1;
//     const strongQuality = Number(quality.score) >= (mode === "enroll" ? 0.6 : 0.45);
//     setChecks({ camera: cameraActive(streamRef.current), face: faceCount === 1, liveness, quality: strongQuality });
//     context.drawImage(video, 0, 0, canvas.width, canvas.height);
//     const imageBase64 = canvas.toDataURL("image/jpeg", 0.88);
//     return {
//       image_base64: imageBase64,
//       liveness_passed: liveness,
//       face_count: faceCount,
//       quality: { ...quality, face_detection_supported: faceSupported },
//       model_version: modelVersion,
//       device_fingerprint: await digest([navigator.userAgent, screen.width, screen.height, Intl.DateTimeFormat().resolvedOptions().timeZone].join("|")),
//     };
//   }

//   async function submit() {
//     setBusy(true); setError(""); setResult(null);
//     try {
//       const payload = await capturePayload();
//       const body = mode === "enroll"
//         ? await request(`/api/v1/evaluator-management/${evaluator?.id}/face/enroll`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
//         : await request("/api/v1/evaluator-management/face/verify-access", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ assignment_id: assignment?.id, ...payload }) });
//       setResult(body);
//       if (mode === "enroll") await onComplete(body);
//     } catch (reason) {
//       setError(reason instanceof Error ? reason.message : "Identity verification failed.");
//     } finally { setBusy(false); }
//   }

//   const target = mode === "enroll" ? evaluator?.display_name : assignment?.script;
//   return <div className="modal-backdrop"><div className="modal-panel identity-verification-modal" role="dialog" aria-modal="true"><header className="modal-header"><div><h2>{mode === "enroll" ? "Face enrollment" : "Identity verification"}</h2><p>{target || "Evaluator"}</p></div><button className="icon-button" title="Close" onClick={onClose}><X /></button></header><div className="identity-verification-body"><div className="identity-camera"><div className="camera-preview">{stream ? <video ref={videoRef} muted playsInline aria-label="Live identity camera preview" /> : <Camera />}<span>{stream && <i />}{stream ? "Camera active" : "Camera required"}</span></div></div><div className="identity-check-list"><div className={checks.camera ? "ready" : "pending"}>{checks.camera ? <Check /> : <Camera />}<span>Camera</span></div><div className={checks.face ? "ready" : "pending"}>{checks.face ? <Check /> : <ScanFace />}<span>Single face</span></div><div className={checks.liveness ? "ready" : "pending"}>{checks.liveness ? <Check /> : <RefreshCw />}<span>Liveness</span></div><div className={checks.quality ? "ready" : "pending"}>{checks.quality ? <Check /> : <ShieldCheck />}<span>Quality</span></div>{mode === "verify" && selfStatus && <div className={selfStatus.enrolled ? "ready" : "blocked"}>{selfStatus.enrolled ? <Check /> : <X />}<span>{selfStatus.enrolled ? `Enrolled: ${selfStatus.evaluator_name || "Evaluator"} (${selfStatus.evaluator_code || selfStatus.evaluator_id})` : "Not enrolled"}</span></div>}</div>{error && <div className="form-error">{error}</div>}{result && <div className="success-banner"><Check />{mode === "enroll" ? "Face template enrolled" : `Matched: ${String(result.evaluator_name || "Evaluator")} (${String(result.evaluator_code || "")})`}</div>}</div><footer className="modal-footer"><button type="button" className="secondary-button" onClick={startCamera} disabled={busy}>{busy ? <RefreshCw className="spin" /> : <Camera />}Camera</button><button className="primary-button" onClick={() => { if (mode === "verify" && result) { void onComplete(result); } else { void submit(); } }} disabled={busy || (!result && (!stream || (mode === "verify" && (!selfStatus || selfStatus.enrolled === false))))}>{busy ? <RefreshCw className="spin" /> : <ScanFace />}{mode === "verify" && result ? "Continue to security check" : mode === "enroll" ? "Enroll" : "Verify"}</button></footer></div></div>;
// }
