"use client";
/* eslint-disable @next/next/no-img-element -- signed five-minute URLs must bypass image optimization caches */
/* eslint-disable react-hooks/set-state-in-effect */
/* eslint-disable react-hooks/exhaustive-deps */

import {
  ArrowRight,
  Camera,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  Clock,
  Columns2,
  Expand,
  Eye,
  FileText,
  Flag,
  Highlighter,
  ImageUp,
  ListFilter,
  Maximize2,
  Menu,
  MessageSquareText,
  Minus,
  Pause,
  Play,
  Plus,
  RectangleHorizontal,
  RefreshCw,
  RotateCw,
  Rows3,
  Save,
  ShieldAlert,
  ShieldCheck,
  Smartphone,
  Sparkles,
  SquareCheckBig,
  Strikethrough,
  Undo2,
  Users,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";
import {
  FormEvent,
  MouseEvent,
  PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { flushSync } from "react-dom";
import { csrfFetch } from "@/lib/api";
import { IdentityVerificationModal } from "@/components/evaluator-identity-verification";
import {
  CameraPreview,
  SecurePreflightDialog,
  SecurityPauseOverlay,
  useSecureEvaluationSession,
} from "@/components/secure-evaluation-session";

type Assignment = {
  id: string;
  script: string;
  paper: string;
  page_count: number;
  valuation_round: number;
  status: string;
  priority: number;
  is_flagged: boolean;
  locked: boolean;
  draft_saved_at: string | null;
  last_opened_at: string | null;
  last_page: number;
  progress_percent: number;
  due_at: string;
  version: number;
};
type Page = {
  page_number: number;
  asset_id: string;
  url: string;
  expires_at: number;
  sha256: string;
  byte_size: number;
  mime_type: string;
};
type Manifest = {
  assignment: Assignment;
  pages: Page[];
  page_count: number;
  mode: string;
  ttl_seconds: number;
};
type Question = {
  id: string;
  number: string;
  max_marks: number;
  required: boolean;
  criteria: {
    id: string;
    code: string;
    description: string;
    max_marks: number;
    step_marks: number[];
    mandatory: boolean;
  }[];
};
type Mark = {
  id: string;
  question_id: string;
  marks: number;
  outcome: string;
  adjustment: string;
  marked_for_review: boolean;
  requires_attention: boolean;
  examiner_confirmed: boolean;
  sequence: number;
};
type Annotation = {
  id: string;
  page_number: number;
  question_id: string | null;
  kind: string;
  geometry: Record<string, number | { x: number; y: number }[]>;
  style: Record<string, string>;
  symbol: string;
};
type PageAnchor = { question_id: string; page_number: number };
type Marking = {
  evaluation: {
    id: string;
    status: string;
    total_marks: number;
    version: number;
    last_question_id: string | null;
    last_page: number;
  };
  workflow: {
    id: string;
    state: string;
    version: number;
    last_page: number;
    latest_draft_sequence: number;
    draft_expires_at: string | null;
  };
  scheme: {
    id: string;
    title: string;
    version: number;
    digest: string;
    guidelines: string;
    instructions: string;
  };
  questions: Question[];
  marks: Mark[];
  annotations: Annotation[];
  comments: {
    id: string;
    question_id: string | null;
    page_number: number | null;
    kind: string;
    body: string;
    created_at: string;
  }[];
  page_anchors: PageAnchor[];
};
type AIAssist = {
  enabled: boolean;
  mode: "disabled" | "assistive" | "autonomous";
  provider: { provider: string; configured: boolean };
  analysis: null | {
    id: string;
    status: "queued" | "running" | "completed" | "low_confidence" | "failed";
    effective_confidence: number | null;
    summary: string;
    error_message: string;
    assessments: {
      question_id: string;
      question: string;
      marks: number;
      confidence: number;
      feedback: string;
      reasoning: string;
    }[];
  };
};
type Filter =
  | "all"
  | "pending"
  | "in_progress"
  | "draft"
  | "completed"
  | "flagged"
  | "priority";
type AnnotationKind =
  | "tick"
  | "cross"
  | "underline"
  | "highlight"
  | "circle"
  | "rectangle"
  | "arrow";
type AnnotationGeometry = Record<string, number | { x: number; y: number }[]>;
type AnnotationDrag = {
  kind: Exclude<AnnotationKind, "tick" | "cross" | "arrow">;
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
};
type ResizeHandle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";
type AnnotationResize = {
  id: string;
  handle: ResizeHandle;
  startX: number;
  startY: number;
  geometry: { x: number; y: number; width: number; height: number };
  current: { x: number; y: number; width: number; height: number };
};
type ContinuityItem = {
  assignmentId: string;
  page: number;
  progress: number;
  savedAt: string;
};
const filters: { key: Filter; label: string }[] = [
  { key: "all", label: "Assigned" },
  { key: "pending", label: "Pending" },
  { key: "in_progress", label: "In progress" },
  { key: "draft", label: "Drafts" },
  { key: "completed", label: "Completed" },
  { key: "flagged", label: "Flagged" },
  { key: "priority", label: "Priority" },
];
const tools: { kind: AnnotationKind; label: string; icon: typeof Check }[] = [
  { kind: "tick", label: "Tick", icon: Check },
  { kind: "cross", label: "Cross", icon: X },
  { kind: "underline", label: "Underline", icon: Strikethrough },
  { kind: "highlight", label: "Highlight", icon: Highlighter },
  { kind: "circle", label: "Circle", icon: Circle },
  { kind: "rectangle", label: "Rectangle", icon: RectangleHorizontal },
  { kind: "arrow", label: "Arrow", icon: ArrowRight },
];
const titleCase = (value: string) =>
  value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
async function api(path: string, options?: RequestInit) {
  const response = await csrfFetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(body.detail || "Evaluation operation failed");
  return body;
}

type SecurityReport = (
  category: string,
  severity: "low" | "medium" | "high" | "critical",
  details?: Record<string, unknown>,
) => Promise<void>;

function useCopyProtection({
  active,
  containerRef,
  sessionId,
  assignmentId,
  pageNumber,
  report,
  onSecurityPause,
  onSoftAlert,
}: {
  active: boolean;
  containerRef: { current: HTMLElement | null };
  sessionId: string | null;
  assignmentId: string | null;
  pageNumber: number;
  report: SecurityReport;
  onSecurityPause: (category: string) => void;
  onSoftAlert: (message: string, durationMs?: number) => void;
}) {
  useEffect(() => {
    if (!active) return;
    const elementContext = (target: EventTarget | null) => {
      const element = target instanceof HTMLElement ? target : null;
      return element
        ? {
            tag: element.tagName.toLowerCase(),
            id: element.id || undefined,
            className:
              typeof element.className === "string"
                ? element.className
                : undefined,
            label:
              element.getAttribute("aria-label") ||
              element.getAttribute("title") ||
              undefined,
          }
        : null;
    };
    const withinViewer = (target: EventTarget | null) => {
      try {
        const container = containerRef.current;
        return Boolean(
          container && target instanceof Node && container.contains(target),
        );
      } catch {
        return false;
      }
    };
    const protectEvent = (target: EventTarget | null) => {
      try {
        const container = containerRef.current;
        const activeElement = document.activeElement;
        return Boolean(
          container &&
          (withinViewer(target) ||
            (activeElement instanceof Node &&
              container.contains(activeElement)) ||
            document.fullscreenElement === container),
        );
      } catch {
        return false;
      }
    };
    const showWarning = (message: string, durationMs?: number) => {
      try {
        onSoftAlert(message, durationMs);
      } catch {
        /* Soft alerts must never block marking actions. */
      }
    };
    const log = (
      category: string,
      severity: "low" | "medium" | "high" | "critical",
      eventType: string,
      target: EventTarget | null,
      details: Record<string, unknown> = {},
    ) => {
      try {
        void report(category, severity, {
          timestamp: new Date().toISOString(),
          session_id: sessionId,
          user_id: null,
          assignment_id: assignmentId,
          event_type: eventType,
          page_number: pageNumber,
          element_context: elementContext(target),
          ...details,
        }).catch(() => undefined);
      } catch {
        /* Security logging must never block marking actions. */
      }
    };
    const blockClipboard = (event: ClipboardEvent) => {
      try {
        if (!protectEvent(event.target)) return;
        event.preventDefault();
        if (event.type !== "paste")
          event.clipboardData?.setData("text/plain", "");
        showWarning(
          event.type === "paste"
            ? "Pasting into the secure evaluation viewer is blocked and has been logged."
            : event.type === "cut"
              ? "Cutting protected evaluation content is blocked and has been logged."
              : "Copying protected evaluation content is blocked and has been logged.",
        );
        log(event.type + "_blocked", "medium", event.type, event.target);
      } catch {
        /* Clipboard protection must fail open so evaluation work can continue. */
      }
    };
    const blockContextMenu = (event: PointerEvent) => {
      try {
        if (!protectEvent(event.target)) return;
        event.preventDefault();
        showWarning(
          "Right-click is disabled during secure evaluation and has been logged.",
        );
        log("context_menu_blocked", "medium", event.type, event.target);
      } catch {
        /* Context-menu protection must fail open. */
      }
    };
    const isPrintScreen = (event: KeyboardEvent) =>
      event.key === "PrintScreen" ||
      event.code === "PrintScreen" ||
      event.keyCode === 44;
    const lockForPrintScreen = (
      event: KeyboardEvent,
      eventType: "keydown" | "keyup",
    ) => {
      try {
        event.preventDefault();
        containerRef.current?.classList.add("security-printscreen-lock");
        void containerRef.current?.offsetHeight;
        const details = {
          timestamp: new Date().toISOString(),
          session_id: sessionId,
          user_id: null,
          assignment_id: assignmentId,
          event_type: eventType,
          page_number: pageNumber,
          element_context: elementContext(event.target),
          trigger: "printscreen",
          key: event.key,
          code: event.code,
          keyCode: event.keyCode,
        };
        flushSync(() => onSecurityPause("viewer_hidden"));
        void report("viewer_hidden", "critical", details).catch(
          () => undefined,
        );
        showWarning(
          "Screenshot shortcut detected. The script is locked and this attempt has been logged.",
          10000,
        );
      } catch {
        /* Screenshot shortcut protection must fail open. */
      }
    };
    const keydown = (event: KeyboardEvent) => {
      try {
        const key = event.key.toLowerCase();
        if (isPrintScreen(event)) {
          lockForPrintScreen(event, "keydown");
          return;
        }
        if (
          (event.ctrlKey || event.metaKey) &&
          ["p", "s", "c", "x", "v"].includes(key)
        ) {
          event.preventDefault();
          showWarning(
            key === "p"
              ? "Printing is disabled during secure evaluation and has been logged."
              : key === "s"
                ? "Saving evaluation content with the browser shortcut is blocked and has been logged."
                : key === "x"
                  ? "Cutting protected evaluation content is blocked and has been logged."
                  : key === "v"
                    ? "Pasting into the secure evaluation viewer is blocked and has been logged."
                    : "Copying protected evaluation content is blocked and has been logged.",
          );
          log(
            "shortcut_" + key + "_blocked",
            key === "p" ? "high" : "medium",
            "keydown",
            event.target,
            { key: event.key },
          );
        }
        if (
          event.key === "F12" ||
          ((event.ctrlKey || event.metaKey) &&
            event.shiftKey &&
            ["i", "j"].includes(key)) ||
          ((event.ctrlKey || event.metaKey) && key === "u")
        ) {
          event.preventDefault();
          showWarning(
            "This browser shortcut is disabled during secure evaluation and has been logged as best-effort protection.",
          );
          log(
            "shortcut_" + (event.key === "F12" ? "f12" : key) + "_blocked",
            "medium",
            "keydown",
            event.target,
            { key: event.key, best_effort: true },
          );
        }
      } catch {
        /* Shortcut protection must fail open. */
      }
    };
    const printScreenKeyup = (event: KeyboardEvent) => {
      if (isPrintScreen(event)) lockForPrintScreen(event, "keyup");
    };
    let lastCriticalLock = 0;
    const baselineWidth = window.innerWidth;
    const baselineHeight = window.innerHeight;
    const criticalLock = (
      trigger: string,
      eventType: string,
      target: EventTarget | null = document.activeElement,
    ) => {
      try {
        const now = Date.now();
        if (now - lastCriticalLock < 1500) return;
        lastCriticalLock = now;
        containerRef.current?.classList.add("security-printscreen-lock");
        void containerRef.current?.offsetHeight;
        const details = {
          timestamp: new Date().toISOString(),
          session_id: sessionId,
          user_id: null,
          assignment_id: assignmentId,
          event_type: eventType,
          page_number: pageNumber,
          element_context: elementContext(target),
          trigger,
          visibility: document.visibilityState,
        };
        flushSync(() => onSecurityPause("viewer_hidden"));
        void report("viewer_hidden", "critical", details).catch(
          () => undefined,
        );
        showWarning(
          "Evaluation paused. Run security checks and re-authenticate to resume.",
          10000,
        );
      } catch {
        /* Critical security locking must not block browser recovery. */
      }
    };
    const visibility = () => {
      if (document.hidden)
        criticalLock(
          "visibility_hidden",
          "visibilitychange",
          document.activeElement,
        );
    };
    const blur = () =>
      criticalLock("window_blur", "blur", document.activeElement);
    const fullscreen = () => {
      if (!document.fullscreenElement)
        criticalLock(
          "fullscreen_exited",
          "fullscreenchange",
          document.activeElement,
        );
    };
    const resize = () => {
      if (
        Math.abs(window.innerWidth - baselineWidth) > 80 ||
        Math.abs(window.innerHeight - baselineHeight) > 80 ||
        !document.fullscreenElement
      )
        criticalLock("window_resized", "resize", document.activeElement);
    };
    document.addEventListener("copy", blockClipboard, true);
    document.addEventListener("cut", blockClipboard, true);
    document.addEventListener("paste", blockClipboard, true);
    document.addEventListener("contextmenu", blockContextMenu, true);
    window.addEventListener("keydown", keydown, true);
    document.addEventListener("keydown", keydown, true);
    window.addEventListener("keyup", printScreenKeyup, true);
    document.addEventListener("keyup", printScreenKeyup, true);
    document.addEventListener("visibilitychange", visibility);
    document.addEventListener("fullscreenchange", fullscreen);
    window.addEventListener("blur", blur);
    window.addEventListener("resize", resize);
    return () => {
      document.removeEventListener("copy", blockClipboard, true);
      document.removeEventListener("cut", blockClipboard, true);
      document.removeEventListener("paste", blockClipboard, true);
      document.removeEventListener("contextmenu", blockContextMenu, true);
      window.removeEventListener("keydown", keydown, true);
      document.removeEventListener("keydown", keydown, true);
      window.removeEventListener("keyup", printScreenKeyup, true);
      document.removeEventListener("keyup", printScreenKeyup, true);
      document.removeEventListener("visibilitychange", visibility);
      document.removeEventListener("fullscreenchange", fullscreen);
      window.removeEventListener("blur", blur);
      window.removeEventListener("resize", resize);
    };
  }, [
    active,
    assignmentId,
    containerRef,
    onSecurityPause,
    onSoftAlert,
    pageNumber,
    report,
    sessionId,
  ]);
}

export function EvaluationWorkspace({
  role,
  user,
}: {
  role: string;
  user?: { name: string; email: string };
}) {
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [marking, setMarking] = useState<Marking | null>(null);
  const [aiAssist, setAIAssist] = useState<AIAssist | null>(null);
  const [aiPanel, setAIPanel] = useState(false);
  const [lockToken, setLockToken] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [visitedPages, setVisitedPages] = useState<Set<number>>(
    () => new Set([1]),
  );
  const [currentQuestion, setCurrentQuestion] = useState("");
  const [zoom, setZoom] = useState(100);
  const [rotation, setRotation] = useState(0);
  const [fit, setFit] = useState<"screen" | "width" | "custom">("screen");
  const [multiPage, setMultiPage] = useState(false);
  const [lowBandwidth, setLowBandwidth] = useState(false);
  const [enhance, setEnhance] = useState(false);
  const [annotationTool, setAnnotationTool] = useState<AnnotationKind | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [online, setOnline] = useState(true);
  const [syncStatus, setSyncStatus] = useState<"saved" | "queued" | "syncing">(
    "saved",
  );
  const [securityCode, setSecurityCode] = useState("");
  const [highlightQuestion, setHighlightQuestion] = useState(false);
  const [selectedAnnotation, setSelectedAnnotation] = useState<string | null>(
    null,
  );
  const [annotationDrag, setAnnotationDrag] = useState<AnnotationDrag | null>(
    null,
  );
  const [annotationResize, setAnnotationResize] =
    useState<AnnotationResize | null>(null);
  const [securityNotice, setSecurityNotice] = useState("");
  const [pagesCollapsed, setPagesCollapsed] = useState(false);
  const [flaggingAssignmentId, setFlaggingAssignmentId] = useState<string | null>(null);
  const viewerRef = useRef<HTMLDivElement>(null);
  const documentStageRef = useRef<HTMLElement>(null);
<<<<<<< HEAD
  const pageRef = useRef<HTMLDivElement>(null);
=======
  const panState = useRef({ active: false, moved: false, startX: 0, startY: 0, scrollLeft: 0, scrollTop: 0 });
  const suppressAnnotationClick = useRef(false);
>>>>>>> dba17c5 (Updated project changes)
  const draftSequence = useRef(0);
  const securityNoticeTimer = useRef<number | null>(null);
  const evaluatorMode = role === "evaluator";
  const evaluatorName = user?.name || user?.email || "Evaluator";
  const evaluatorInitials =
    evaluatorName
      .split(" ")
      .map((part) => part[0])
      .join("")
      .slice(0, 2)
      .toUpperCase() || "EV";
  const security = useSecureEvaluationSession();
  const changeZoom = useCallback((next: number) => {
    setFit("custom");
    setZoom(Math.max(40, Math.min(next, 250)));
  }, []);
  useEffect(() => {
    if (fit !== "custom") return;
    const stage = documentStageRef.current;
    if (!stage) return;
    const before = {
      scrollWidth: stage.scrollWidth,
      clientWidth: stage.clientWidth,
      scrollLeft: stage.scrollLeft,
      scrollHeight: stage.scrollHeight,
      clientHeight: stage.clientHeight,
      scrollTop: stage.scrollTop,
    };
    const frame = window.requestAnimationFrame(() => {
      stage.scrollLeft = Math.max(0, (stage.scrollWidth - stage.clientWidth) / 2);
      stage.scrollTop = Math.max(0, (stage.scrollHeight - stage.clientHeight) / 2);
      console.debug("[Evaluation zoom]", { before, after: {
        scrollWidth: stage.scrollWidth,
        clientWidth: stage.clientWidth,
        scrollLeft: stage.scrollLeft,
        scrollHeight: stage.scrollHeight,
        clientHeight: stage.clientHeight,
        scrollTop: stage.scrollTop,
      }});
    });
    return () => window.cancelAnimationFrame(frame);
  }, [fit, zoom]);
  const [pendingAssignment, setPendingAssignment] = useState<Assignment | null>(
    null,
  );
  const [identityReadyAssignmentId, setIdentityReadyAssignmentId] =
    useState("");
  const load = useCallback(async () => {
    try {
      const body = await api("/api/v1/allocation/catalog");
      setAssignments(body.assignments || []);
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Evaluation desk could not be loaded",
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  const refreshMarking = useCallback(async (assignmentId: string) => {
    const body = (await api(
      `/api/v1/marking/assignments/${assignmentId}/workspace`,
    )) as Marking;
    draftSequence.current = Math.max(
      draftSequence.current,
      body.workflow.latest_draft_sequence,
    );
    setMarking(body);
    setCurrentQuestion(
      body.evaluation.last_question_id || body.questions[0]?.id || "",
    );
    return body;
  }, []);
  const loadAIAssist = useCallback(async (assignmentId: string) => {
    try {
      const body = (await api(
        `/api/v1/marking/assignments/${assignmentId}/ai-analysis`,
      )) as AIAssist;
      setAIAssist(body);
      return body;
    } catch {
      setAIAssist(null);
      return null;
    }
  }, []);
  const visible = useMemo(
    () =>
      assignments.filter(
        (item) =>
          filter === "all" ||
          (filter === "pending" &&
            ["assigned", "accepted", "reassigned"].includes(item.status)) ||
          (filter === "in_progress" && item.status === "in_progress") ||
          (filter === "draft" &&
            Boolean(item.draft_saved_at) &&
            item.status !== "submitted") ||
          (filter === "completed" && item.status === "submitted") ||
          (filter === "flagged" && item.is_flagged) ||
          (filter === "priority" && item.priority >= 4),
      ),
    [assignments, filter],
  );
  const activeIndex = manifest
    ? visible.findIndex((item) => item.id === manifest.assignment.id)
    : -1;
  const toggleAssignmentFlag = useCallback(
    async (item: Assignment, nextFlagged = !item.is_flagged) => {
      setFlaggingAssignmentId(item.id);
      const previous = item.is_flagged;
      setAssignments((current) =>
        current.map((candidate) =>
          candidate.id === item.id
            ? { ...candidate, is_flagged: nextFlagged }
            : candidate,
        ),
      );
      setManifest((current) =>
        current && current.assignment.id === item.id
          ? { ...current, assignment: { ...current.assignment, is_flagged: nextFlagged } }
          : current,
      );
      try {
        const updated = await api(
          `/api/v1/allocation/assignments/${item.id}/flag`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              version: item.version,
              flagged: nextFlagged,
              reason: nextFlagged ? "Evaluation review flag" : "",
            }),
          },
        );
        setAssignments((current) =>
          current.map((candidate) => candidate.id === item.id ? updated : candidate),
        );
        setManifest((current) =>
          current && current.assignment.id === item.id
            ? { ...current, assignment: { ...current.assignment, ...updated } }
            : current,
        );
      } catch (reason) {
        setAssignments((current) =>
          current.map((candidate) =>
            candidate.id === item.id
              ? { ...candidate, is_flagged: previous }
              : candidate,
          ),
        );
        setManifest((current) =>
          current && current.assignment.id === item.id
            ? { ...current, assignment: { ...current.assignment, is_flagged: previous } }
            : current,
        );
        setError(reason instanceof Error ? reason.message : "Script flag could not be saved");
      } finally {
        setFlaggingAssignmentId(null);
      }
    },
    [],
  );
  const syncQuestionFlagToAssignment = useCallback(
    async (flagged: boolean) => {
      if (!manifest) return;
      const item = manifest.assignment;
      await toggleAssignmentFlag(item, flagged);
    },
    [manifest, toggleAssignmentFlag],
  );
  async function openAssignment(
    item: Assignment,
    bandwidth = lowBandwidth,
    securityReady = false,
  ) {
    if (evaluatorMode && !securityReady) {
      setError("");
      try {
        const policy = await api("/api/v1/phase4/remote-security/policy");
        setIdentityReadyAssignmentId(
          policy.identity_verification_required === false ? item.id : "",
        );
        setPendingAssignment(item);
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Security policy could not be loaded",
        );
      }
      return;
    }
    setViewerLoading(true);
    setError("");
    let acquiredToken = "";
    try {
      let current = item;
      if (evaluatorMode && item.status !== "submitted") {
        const lock = await api(
          `/api/v1/assignment-governance/assignments/${item.id}/lock`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version: item.version }),
          },
        );
        acquiredToken = lock.token;
      }
      if (item.status !== "submitted")
        current = await api(
          `/api/v1/allocation/assignments/${item.id}/action`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version: item.version, action: "start" }),
          },
        );
      if (evaluatorMode) {
        if (current.status !== "submitted")
          await api(`/api/v1/marking/assignments/${item.id}/open`, {
            method: "POST",
          });
        await refreshMarking(item.id);
        await loadAIAssist(item.id);
      }
      const viewer = await api(
        `/api/v1/allocation/assignments/${item.id}/viewer?low_bandwidth=${bandwidth}`,
      );
      viewer.assignment = current;
      setLockToken(acquiredToken);
      setCurrentPage(
        Math.min(
          Math.max(current.last_page || 1, 1),
          Math.max(viewer.page_count, 1),
        ),
      );
      setVisitedPages(new Set([Math.max(current.last_page || 1, 1)]));
      setZoom(100);
      setRotation(0);
      setFit("screen");
      setAnnotationTool(null);
      setManifest(viewer);
      await load();
    } catch (reason) {
      if (acquiredToken) {
        try {
          await api(
            `/api/v1/assignment-governance/assignments/${item.id}/unlock`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                token: acquiredToken,
                reason: "viewer setup failed",
              }),
            },
          );
        } catch {
          /* Lock expiry is already a complete cleanup. */
        }
      }
      setMarking(null);
      setAIAssist(null);
      setError(
        reason instanceof Error
          ? reason.message
          : "Secure viewer could not open",
      );
      await security.finish(false);
    } finally {
      setViewerLoading(false);
    }
  }
  async function startSecureEvaluation(consent: boolean) {
    if (!pendingAssignment) return;
    const item = pendingAssignment;
    try {
      await security.start(item.id, consent);
      setPendingAssignment(null);
      await openAssignment(item, lowBandwidth, true);
    } catch {
      /* Security controller displays the actionable reason. */
    }
  }
  async function cancelPreflight() {
    setPendingAssignment(null);
    setIdentityReadyAssignmentId("");
    await security.finish(false);
  }
  async function reloadViewerPages(bandwidth: boolean) {
    if (!manifest) return;
    setViewerLoading(true);
    setError("");
    try {
      const viewer = await api(
        `/api/v1/allocation/assignments/${manifest.assignment.id}/viewer?low_bandwidth=${bandwidth}`,
      );
      setManifest((current) =>
        current ? { ...viewer, assignment: current.assignment } : current,
      );
      setLowBandwidth(bandwidth);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Protected pages could not be reloaded",
      );
    } finally {
      setViewerLoading(false);
    }
  }
  async function closeViewer() {
    const current = manifest;
    const token = lockToken;
    if (current && current.assignment.status !== "submitted")
      await saveProgress(currentPage);
    setManifest(null);
    setMarking(null);
    setAIAssist(null);
    setAIPanel(false);
    setLockToken("");
    setAnnotationTool(null);
    setSelectedAnnotation(null);
    if (current && token && current.assignment.status !== "submitted") {
      try {
        await api(
          `/api/v1/assignment-governance/assignments/${current.assignment.id}/unlock`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token, reason: "viewer closed" }),
          },
        );
      } catch {
        /* Expired and submission-released locks need no follow-up. */
      }
    }
    if (evaluatorMode) await security.finish(false);
    await load();
  }
  const saveProgress = useCallback(
    async (page: number) => {
      if (!manifest || manifest.assignment.status === "submitted") return;
      const nextVisitedPages = new Set(visitedPages);
      nextVisitedPages.add(page);
      const requiredQuestionIds = new Set(
        marking?.questions.filter((item) => item.required).map((item) => item.id) || [],
      );
      const savedRequiredMarks = new Set(
        marking?.marks
          .filter((item) => requiredQuestionIds.has(item.question_id))
          .map((item) => item.question_id) || [],
      ).size;
      const pageCompletion = nextVisitedPages.size / Math.max(manifest.page_count, 1);
      const markCompletion = requiredQuestionIds.size
        ? savedRequiredMarks / requiredQuestionIds.size
        : 1;
      const progress = Math.min(
        nextVisitedPages.size >= manifest.page_count &&
          savedRequiredMarks >= requiredQuestionIds.size
          ? 100
          : 99,
        Math.round(((pageCompletion + markCompletion) / 2) * 100),
      );
      if (!navigator.onLine) {
        const stored = JSON.parse(
          localStorage.getItem("admiezo-continuity-queue") || "[]",
        ) as ContinuityItem[];
        const next = [
          ...stored.filter(
            (item) => item.assignmentId !== manifest.assignment.id,
          ),
          {
            assignmentId: manifest.assignment.id,
            page,
            progress,
            savedAt: new Date().toISOString(),
          },
        ];
        localStorage.setItem("admiezo-continuity-queue", JSON.stringify(next));
        setManifest((current) =>
          current
            ? {
                ...current,
                assignment: {
                  ...current.assignment,
                  last_page: page,
                  progress_percent: progress,
                },
              }
            : current,
        );
        setSyncStatus("queued");
        setNotice(
          "Connection lost. Page progress is queued locally; marks and script content are not stored.",
        );
        return;
      }
      setSaving(true);
      try {
        const updated = await api(
          `/api/v1/allocation/assignments/${manifest.assignment.id}/action`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              version: manifest.assignment.version,
              action: "progress",
              page,
              progress_percent: progress,
            }),
          },
        );
        setManifest((current) =>
          current ? { ...current, assignment: updated } : current,
        );
        if (marking && lockToken) {
          draftSequence.current += 1;
          const draft = await api(
            `/api/v1/workflow/assignments/${manifest.assignment.id}/drafts`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Assignment-Lock": lockToken,
              },
              body: JSON.stringify({
                version: marking.workflow.version,
                client_sequence: draftSequence.current,
                last_question_id: currentQuestion || null,
                last_page: page,
                ui_state: {
                  zoom,
                  rotation,
                  active_tool: annotationTool,
                  fit,
                  sidebar: "marking",
                },
              }),
            },
          );
          setMarking((current) =>
            current
              ? {
                  ...current,
                  workflow: {
                    ...current.workflow,
                    version: draft.version,
                    last_page: page,
                    latest_draft_sequence: draftSequence.current,
                  },
                }
              : current,
          );
          setManifest((current) =>
            current
              ? {
                  ...current,
                  assignment: {
                    ...current.assignment,
                    version: draft.assignment_version,
                    draft_saved_at: new Date().toISOString(),
                    last_page: page,
                  },
                }
              : current,
          );
        }
        setSyncStatus("saved");
        await load();
      } catch (reason) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Viewer position could not be saved",
        );
      } finally {
        setSaving(false);
      }
    },
    [
      annotationTool,
      currentQuestion,
      fit,
      load,
      lockToken,
      manifest,
      marking,
      rotation,
      zoom,
    ],
  );
  const goTo = useCallback(
    (page: number) => {
      if (security.paused) return;
      const next = Math.min(
        Math.max(page, 1),
        Math.max(manifest?.page_count || 1, 1),
      );
      setCurrentPage(next);
      setVisitedPages((previous) => {
        const updated = new Set(previous);
        updated.add(next);
        return updated;
      });
      void saveProgress(next);
    },
    [manifest?.page_count, saveProgress, security.paused],
  );
  const pageForQuestion = useCallback(
    (id: string) => {
      if (!marking) return null;
      const anchor = marking.page_anchors.find(
        (item) => item.question_id === id,
      );
      if (anchor) return anchor.page_number;
      const annotation = marking.annotations
        .filter((item) => item.question_id === id)
        .at(-1);
      if (annotation) return annotation.page_number;
      const comment = marking.comments
        .filter((item) => item.question_id === id && item.page_number !== null)
        .at(-1);
      return comment?.page_number ?? null;
    },
    [marking],
  );
  const selectQuestion = useCallback(
    (id: string) => {
      setCurrentQuestion(id);
    },
    [],
  );
  async function pinQuestionPage(questionId: string, page: number) {
    if (!marking || !manifest || !lockToken) return;
    setSaving(true);
    try {
      await api(
        `/api/v1/marking/evaluations/${marking.evaluation.id}/questions/${questionId}/page-anchor`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Assignment-Lock": lockToken,
          },
          body: JSON.stringify({
            version: marking.evaluation.version,
            page_number: page,
          }),
        },
      );
      await refreshMarking(manifest.assignment.id);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Question page could not be pinned",
      );
    } finally {
      setSaving(false);
    }
  }
  useEffect(() => {
    if (!manifest) return;
    function onKey(event: KeyboardEvent) {
      if (
        (event.target as HTMLElement).matches("input, textarea, select") ||
        security.paused
      )
        return;
      if (event.key === "ArrowRight" || event.key === "PageDown")
        goTo(currentPage + 1);
      else if (event.key === "ArrowLeft" || event.key === "PageUp")
        goTo(currentPage - 1);
      else if (event.key === "+" || event.key === "=") {
        changeZoom(zoom + 10);
      } else if (event.key === "-") {
        changeZoom(zoom - 10);
      } else if (event.key.toLowerCase() === "r")
        setRotation((value) => (value + 90) % 360);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [manifest, currentPage, goTo, security.paused, changeZoom, zoom]);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  const showSecurityNotice = useCallback(
    (message: string, durationMs = 3500) => {
      setSecurityNotice(message);
      if (securityNoticeTimer.current)
        window.clearTimeout(securityNoticeTimer.current);
      securityNoticeTimer.current = window.setTimeout(
        () => setSecurityNotice(""),
        durationMs,
      );
    },
    [],
  );
  useEffect(() => {
    if (!security.paused)
      viewerRef.current?.classList.remove("security-printscreen-lock");
  }, [security.paused]);
  useEffect(
    () => () => {
      if (securityNoticeTimer.current)
        window.clearTimeout(securityNoticeTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (!manifest || !online) return;
    const queued = JSON.parse(
      localStorage.getItem("admiezo-continuity-queue") || "[]",
    ) as ContinuityItem[];
    const pending = queued.find(
      (item) => item.assignmentId === manifest.assignment.id,
    );
    if (!pending) return;
    setSyncStatus("syncing");
    api(`/api/v1/allocation/assignments/${pending.assignmentId}/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        version: manifest.assignment.version,
        action: "progress",
        page: pending.page,
        progress_percent: pending.progress,
      }),
    })
      .then((updated) => {
        localStorage.setItem(
          "admiezo-continuity-queue",
          JSON.stringify(
            queued.filter((item) => item.assignmentId !== pending.assignmentId),
          ),
        );
        setManifest((current) =>
          current ? { ...current, assignment: updated } : current,
        );
        setSyncStatus("saved");
        setNotice("Queued page progress synchronized.");
      })
      .catch(() => {
        setSyncStatus("queued");
        setNotice(
          "Queued page progress is waiting for a fresh server version.",
        );
      });
  }, [manifest?.assignment.id, online]);
  useEffect(() => {
    if (!manifest || !evaluatorMode) return;
    setSecurityCode(
      (security.session?.id || "SECURE").slice(0, 8).toUpperCase(),
    );
    void security.report("viewer_opened", "low");
  }, [manifest?.assignment.id, evaluatorMode, security.session?.id]);
  useEffect(() => {
    if (
      !manifest ||
      !aiAssist?.analysis ||
      !["queued", "running"].includes(aiAssist.analysis.status)
    )
      return;
    const poll = window.setInterval(
      () => void loadAIAssist(manifest.assignment.id),
      2500,
    );
    return () => window.clearInterval(poll);
  }, [aiAssist?.analysis?.status, loadAIAssist, manifest?.assignment.id]);
  useCopyProtection({
    active: Boolean(manifest && evaluatorMode),
    containerRef: viewerRef,
    sessionId: security.session?.id || null,
    assignmentId: manifest?.assignment.id || null,
    pageNumber: currentPage,
    report: security.report,
    onSecurityPause: security.pause,
    onSoftAlert: showSecurityNotice,
  });

  async function requestAIAnalysis() {
    if (!manifest || !aiAssist?.enabled) return;
    setSaving(true);
    setError("");
    setAIPanel(true);
    try {
      await api(
        `/api/v1/marking/assignments/${manifest.assignment.id}/ai-analysis`,
        { method: "POST" },
      );
      await loadAIAssist(manifest.assignment.id);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "AI analysis could not be started",
      );
    } finally {
      setSaving(false);
    }
  }
<<<<<<< HEAD
  const pointFromEvent = (
    event: { clientX: number; clientY: number },
    rect: DOMRect,
  ) => ({
    x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
    y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
  });
  const geometryFromDrag = (
    kind: Exclude<AnnotationKind, "tick" | "cross" | "arrow">,
    startX: number,
    startY: number,
    endX: number,
    endY: number,
  ) => {
    const x = Math.min(startX, endX);
    const y = kind === "underline" ? startY : Math.min(startY, endY);
    const width = Math.abs(endX - startX);
    const height = kind === "underline" ? 0.01 : Math.abs(endY - startY);
    return { x, y, width, height };
  };
  async function saveAnnotation(
    kind: AnnotationKind,
    geometry: AnnotationGeometry,
    target = marking?.evaluation,
    source?: Annotation,
  ) {
    if (!marking || !manifest || !lockToken || !target) return;

=======
  async function addAnnotation(event: MouseEvent<HTMLDivElement>) {
    if (
      suppressAnnotationClick.current ||
      !annotationTool ||
      !marking ||
      !manifest ||
      !lockToken ||
      security.paused
    )
      return;
    const rect = event.currentTarget.getBoundingClientRect();
    const rawX = Math.max(
      0,
      Math.min(1, (event.clientX - rect.left) / rect.width),
    );
    const rawY = Math.max(
      0,
      Math.min(1, (event.clientY - rect.top) / rect.height),
    );
    let geometry: Record<string, number | { x: number; y: number }[]>;
    if (annotationTool === "arrow") {
      geometry = {
        points: [
          { x: Math.max(0, rawX - 0.08), y: rawY },
          { x: rawX, y: rawY },
        ],
      };
    } else {
      const isSymbol = ["tick", "cross"].includes(annotationTool);
      const boxX = isSymbol ? rawX : Math.max(0, rawX - 0.06);
      const boxY = isSymbol ? rawY : Math.max(0, rawY - 0.025);
      const width = isSymbol
        ? 0.04
        : Math.min(0.12, 1 - Math.max(0, rawX - 0.06));
      const height = isSymbol
        ? 0.04
        : Math.min(0.05, 1 - Math.max(0, rawY - 0.025));
      const safe = getSafeAnnotationPosition(
        { x: boxX, y: boxY, width, height },
        pageAnnotations
          .filter((item) => item.kind !== "arrow")
          .map((item) => ({
            x: Number(item.geometry.x || 0),
            y: Number(item.geometry.y || 0),
            width: Number(item.geometry.width || 0.04),
            height: Number(item.geometry.height || 0.04),
          })),
        15 / rect.height,
      );
      geometry = isSymbol
        ? { x: safe.x, y: safe.y }
        : { x: safe.x, y: safe.y, width, height };
    }
>>>>>>> dba17c5 (Updated project changes)
    setSaving(true);
    try {
      await api(
        `/api/v1/marking/evaluations/${target.id}/annotations`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Assignment-Lock": lockToken,
          },
          body: JSON.stringify({
            version: target.version,
            page_number: source?.page_number || currentPage,
            question_id: source?.question_id || currentQuestion || null,
            kind,
            geometry,
            style: source?.style || {
              color: kind === "highlight" ? "#f6d85f" : "#c83232",
              width: "2",
            },
            symbol: source?.symbol || "",
          }),
        },
      );
      await refreshMarking(manifest.assignment.id);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Annotation could not be saved",
      );
    } finally {
      setSaving(false);
    }
  }
<<<<<<< HEAD
  async function addAnnotation(event: MouseEvent<HTMLDivElement>) {
    if (
      !annotationTool ||
      !["tick", "cross", "arrow"].includes(annotationTool) ||
      !marking ||
      !manifest ||
      !lockToken ||
      security.paused
    )
      return;
    const rect = event.currentTarget.getBoundingClientRect();
    const point = pointFromEvent(event, rect);
    const geometry = annotationTool === "arrow"
      ? { points: [{ x: Math.max(0, point.x - 0.08), y: point.y }, point] }
      : point;
    await saveAnnotation(annotationTool, geometry);
  }
  function startAnnotationDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (
      !annotationTool ||
      !["underline", "highlight", "circle", "rectangle"].includes(annotationTool) ||
      security.paused
    )
      return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const point = pointFromEvent(event, event.currentTarget.getBoundingClientRect());
    setAnnotationDrag({
      kind: annotationTool as Exclude<AnnotationKind, "tick" | "cross" | "arrow">,
      startX: point.x,
      startY: point.y,
      currentX: point.x,
      currentY: point.y,
    });
  }
  function updateAnnotationDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!annotationDrag) return;
    const point = pointFromEvent(event, event.currentTarget.getBoundingClientRect());
    setAnnotationDrag((current) =>
      current ? { ...current, currentX: point.x, currentY: point.y } : null,
    );
  }
  async function finishAnnotationDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!annotationDrag) return;
    const point = pointFromEvent(event, event.currentTarget.getBoundingClientRect());
    const geometry = geometryFromDrag(
      annotationDrag.kind,
      annotationDrag.startX,
      annotationDrag.startY,
      point.x,
      point.y,
    );
    setAnnotationDrag(null);
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    await saveAnnotation(annotationDrag.kind, geometry);
  }
  function startAnnotationResize(
    id: string,
    handle: ResizeHandle,
    event: ReactPointerEvent<HTMLSpanElement>,
  ) {
    const target = pageAnnotations.find((item) => item.id === id);
    const rect = pageRef.current?.getBoundingClientRect();
    if (!target || !rect || !marking || security.paused) return;
    event.preventDefault();
    event.stopPropagation();
    const geometry = {
      x: Number(target.geometry.x || 0),
      y: Number(target.geometry.y || 0),
      width: Number(target.geometry.width || 0.01),
      height: Number(target.geometry.height || 0.01),
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setSelectedAnnotation(id);
    setAnnotationResize({
      id,
      handle,
      startX: event.clientX,
      startY: event.clientY,
      geometry,
      current: geometry,
    });
  }
  function updateAnnotationResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!annotationResize) return;
    const rect = pageRef.current?.getBoundingClientRect();
    if (!rect) return;
    const dx = (event.clientX - annotationResize.startX) / rect.width;
    const dy = (event.clientY - annotationResize.startY) / rect.height;
    const original = annotationResize.geometry;
    let x = original.x;
    let y = original.y;
    let width = original.width;
    let height = original.height;
    if (annotationResize.handle.includes("w")) {
      x = Math.max(0, Math.min(1, original.x + dx));
      width = original.width - dx;
    }
    if (annotationResize.handle.includes("e")) width = original.width + dx;
    if (annotationResize.handle.includes("n")) {
      y = Math.max(0, Math.min(1, original.y + dy));
      height = original.height - dy;
    }
    if (annotationResize.handle.includes("s")) height = original.height + dy;
    if (annotationResize.handle === "n" || annotationResize.handle === "s") {
      x = original.x;
      width = original.width;
    }
    if (annotationResize.handle === "e" || annotationResize.handle === "w") {
      y = original.y;
      height = original.height;
    }
    setAnnotationResize((current) =>
      current
        ? {
            ...current,
            current: {
              x: Math.max(0, Math.min(1, x)),
              y: Math.max(0, Math.min(1, y)),
              width: Math.max(0.001, Math.min(1 - x, width)),
              height: Math.max(0.001, Math.min(1 - y, height)),
            },
          }
        : null,
    );
  }
  async function finishAnnotationResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!annotationResize) return;
    const target = pageAnnotations.find((item) => item.id === annotationResize.id);
    const geometry = annotationResize.current;
    setAnnotationResize(null);
    if (!target || !marking || !manifest || !lockToken) return;
    setSaving(true);
    try {
      const deleted = await api(
        `/api/v1/marking/evaluations/${marking.evaluation.id}/annotations/${target.id}/action`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Assignment-Lock": lockToken },
          body: JSON.stringify({ version: marking.evaluation.version, action: "delete" }),
        },
      );
      await saveAnnotation(target.kind as AnnotationKind, geometry, {
        ...marking.evaluation,
        version: deleted.version,
      }, target);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Annotation could not be resized");
    } finally {
      setSaving(false);
    }
=======
  function beginDocumentPan(event: ReactPointerEvent<HTMLElement>) {
    if (annotationTool || event.button !== 0) return;
    const stage = event.currentTarget;
    panState.current = { active: true, moved: false, startX: event.clientX, startY: event.clientY, scrollLeft: stage.scrollLeft, scrollTop: stage.scrollTop };
    stage.setPointerCapture(event.pointerId);
  }
  function moveDocumentPan(event: ReactPointerEvent<HTMLElement>) {
    const state = panState.current;
    if (!state.active) return;
    const stage = event.currentTarget;
    const deltaX = event.clientX - state.startX;
    const deltaY = event.clientY - state.startY;
    if (Math.abs(deltaX) > 3 || Math.abs(deltaY) > 3) state.moved = true;
    stage.scrollLeft = state.scrollLeft - deltaX;
    stage.scrollTop = state.scrollTop - deltaY;
    if (state.moved) event.preventDefault();
  }
  function endDocumentPan(event: ReactPointerEvent<HTMLElement>) {
    const state = panState.current;
    if (!state.active) return;
    if (state.moved) suppressAnnotationClick.current = true;
    panState.current.active = false;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    if (state.moved) window.setTimeout(() => { suppressAnnotationClick.current = false; }, 0);
>>>>>>> dba17c5 (Updated project changes)
  }
  async function undoAnnotation() {
    const target = marking?.annotations
      .filter((item) => item.page_number === currentPage)
      .at(-1);
    if (!target || !marking || !manifest) return;
    setSaving(true);
    try {
      await api(
        `/api/v1/marking/evaluations/${marking.evaluation.id}/annotations/${target.id}/action`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Assignment-Lock": lockToken,
          },
          body: JSON.stringify({
            version: marking.evaluation.version,
            action: "delete",
          }),
        },
      );
      await refreshMarking(manifest.assignment.id);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Annotation could not be removed",
      );
    } finally {
      setSaving(false);
    }
  }
  async function submitEvaluation() {
    if (!marking || !manifest || security.paused) return;
    if (security.policy?.camera_required && security.monitoringStatus.camera !== "ok") {
      setError("A live webcam is required before valuation can be submitted.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const submitted = await api(
        `/api/v1/workflow/evaluations/${marking.evaluation.id}/submit`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Assignment-Lock": lockToken,
          },
          body: JSON.stringify({
            evaluation_version: marking.evaluation.version,
            workflow_version: marking.workflow.version,
          }),
        },
      );
      setNotice(
        `Evaluation submitted at ${submitted.total_marks} marks and valuation result locked`,
      );
      setLockToken("");
      setManifest(null);
      setMarking(null);
      setAnnotationTool(null);
      await security.finish(true);
      await load();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Evaluation could not be submitted",
      );
    } finally {
      setSaving(false);
    }
  }
  const page = manifest?.pages.find((item) => item.page_number === currentPage);
  const pageAnnotations =
    marking?.annotations.filter((item) => item.page_number === currentPage) ||
    [];
  const flaggedQuestions = new Set(
    marking?.marks
      .filter((item) => item.marked_for_review)
      .map((item) => item.question_id) || [],
  );
  const [clockNow, setClockNow] = useState<number | null>(null);
  useEffect(() => {
    if (!manifest) {
      setClockNow(null);
      return;
    }
    const updateClock = () => setClockNow(Date.now());
    updateClock();
    const timer = window.setInterval(updateClock, 1000);
    return () => window.clearInterval(timer);
  }, [manifest?.assignment.due_at]);
  const timeLeft = useMemo(() => {
    if (!manifest || clockNow === null) return "--:--:--";
    const ms = Math.max(
      0,
      new Date(manifest.assignment.due_at).getTime() - clockNow,
    );
    const h = Math.floor(ms / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    const sec = Math.floor((ms % 60000) / 1000);
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }, [clockNow, manifest]);
  const syncLabel = saving
    ? "Saving"
    : syncStatus === "queued"
      ? "Queued"
      : syncStatus === "syncing"
        ? "Syncing"
        : "Saved";
  const [monitoringOpen, setMonitoringOpen] = useState(() => {
    try {
      return localStorage.getItem("admiezo-monitoring-live-open") !== "0";
    } catch {
      return true;
    }
  });
  const [dashboardWidth, setDashboardWidth] = useState(() => {
    try {
      return Math.min(
        520,
        Math.max(
          320,
          Number(
            localStorage.getItem("admiezo-evaluation-dashboard-width") || 405,
          ),
        ),
      );
    } catch {
      return 405;
    }
  });
  const dashboardMaxWidth = () =>
    Math.min(520, Math.max(320, Math.floor(window.innerWidth * 0.4)));
  const startDashboardResize = useCallback(
    (event: React.PointerEvent) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = dashboardWidth;
      const move = (moveEvent: PointerEvent) =>
        setDashboardWidth(
          Math.min(
            dashboardMaxWidth(),
            Math.max(320, startWidth - (moveEvent.clientX - startX)),
          ),
        );
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    [dashboardWidth],
  );
  const cameraLabel =
    security.monitoringStatus.camera === "ok"
      ? "OK"
      : security.monitoringStatus.camera === "error"
        ? "Error"
        : "Required";
  const multipleFacesLabel =
    security.monitoringStatus.multipleFaces === "detected"
      ? "Detected"
      : "None";
  const mobileLabel =
    security.pauseReason === "phone_detected" ? "Detected" : "Not Detected";
  useEffect(() => {
    try {
      localStorage.setItem(
        "admiezo-monitoring-live-open",
        monitoringOpen ? "1" : "0",
      );
    } catch {
      /* Monitoring preference is optional. */
    }
  }, [monitoringOpen]);
  useEffect(() => {
    try {
      localStorage.setItem(
        "admiezo-evaluation-dashboard-width",
        String(dashboardWidth),
      );
    } catch {
      /* Dashboard width preference is optional. */
    }
  }, [dashboardWidth]);
  if (manifest)
    return (
      <div
        className="evaluation-viewer"
        ref={viewerRef}
        onContextMenu={(event) => evaluatorMode && event.preventDefault()}
      >
        {security.paused && (
          <SecurityPauseOverlay controller={security} onClose={closeViewer} />
        )}
        <div className="security-watermark" aria-hidden="true">
          ADMIEZO · {manifest.assignment.script} · {securityCode || "SECURE"}
        </div>
        <header className="viewer-header evaluation-desk-header">
          <button
            className="icon-button"
            title="Close viewer"
            onClick={closeViewer}
          >
            <X />
          </button>
          <div className="viewer-title">
            <div>
              <strong>
                {marking?.evaluation.id.slice(0, 12).toUpperCase() ||
                  manifest.assignment.id.slice(0, 12).toUpperCase()}
              </strong>
              <span>{manifest.assignment.paper}</span>
            </div>
          </div>
          <div className="viewer-progress">
            <div>
              <span
                style={{ width: `${manifest.assignment.progress_percent}%` }}
              />
            </div>
            <small>{manifest.assignment.progress_percent}% reviewed</small>
          </div>
          {aiAssist?.enabled && (
            <button
              className="ai-assist-button"
              title="Analyze this script with ADMIEZO AI Assistant"
              onClick={() =>
                aiAssist.analysis
                  ? setAIPanel(true)
                  : void requestAIAnalysis()
              }
              disabled={saving || !aiAssist.provider.configured}
            >
              <Sparkles />
              AI analysis
            </button>
          )}
          {evaluatorMode && <CameraPreview stream={security.stream} compact />}
          <span className={`viewer-network ${online ? "online" : "offline"}`}>
            {online ? <Wifi /> : <WifiOff />}
            {online ? "Connected" : "Continuity"}
          </span>
          {evaluatorMode && (
            <>
              <span className="viewer-secure" title="Restricted secure session">
                <ShieldAlert />
                {securityCode || "Secure"}
              </span>
              {timeLeft !== "--:--:--" && (
                <span className="viewer-timer">
                  <Clock />
                  {timeLeft}
                </span>
              )}
            </>
          )}
          <button
            className="icon-button"
            title="Previous assigned script"
            disabled={activeIndex <= 0}
            onClick={() =>
              void closeViewer().then(() =>
                openAssignment(visible[activeIndex - 1]),
              )
            }
          >
            <ChevronLeft />
          </button>
          <button
            className="icon-button"
            title="Next assigned script"
            disabled={activeIndex < 0 || activeIndex >= visible.length - 1}
            onClick={() =>
              void closeViewer().then(() =>
                openAssignment(visible[activeIndex + 1]),
              )
            }
          >
            <ChevronRight />
          </button>
          <button
            className="icon-button"
            title="Full screen"
            onClick={() => viewerRef.current?.requestFullscreen()}
          >
            <Expand />
          </button>
        </header>
        <div
          className="viewer-toolbar"
          role="toolbar"
          aria-label="Document viewer controls"
        >
          <div className="tool-group">
            <button
              title="Previous page"
              onClick={() => goTo(currentPage - 1)}
              disabled={currentPage <= 1}
            >
              <ChevronLeft />
            </button>
            <label>
              Page {currentPage} of {manifest.page_count}
            </label>
            <button
              title="Next page"
              onClick={() => goTo(currentPage + 1)}
              disabled={currentPage >= manifest.page_count}
            >
              <ChevronRight />
            </button>
          </div>
          <div className="tool-group">
            <button title="Zoom out" onClick={() => changeZoom(zoom - 10)}>
              <Minus />
            </button>
            <span>{zoom}%</span>
            <button title="Zoom in" onClick={() => changeZoom(zoom + 10)}>
              <Plus />
            </button>
            <button
              title="Fit to screen"
              className={fit === "screen" ? "active" : ""}
              onClick={() => setFit("screen")}
            >
              <Eye />
            </button>
            <button
              title="Fit to width"
              className={fit === "width" ? "active" : ""}
              onClick={() => setFit("width")}
            >
              <Maximize2 />
            </button>
          </div>
        </div>
        {securityNotice && (
          <div
            role="alert"
            aria-live="assertive"
            style={{
              position: "fixed",
              top: "96px",
              left: "50%",
              transform: "translateX(-50%)",
              zIndex: 10000,
              maxWidth: "min(720px, calc(100vw - 32px))",
              padding: "14px 18px",
              borderRadius: "12px",
              background: "#7f1d1d",
              color: "white",
              boxShadow: "0 18px 50px rgba(0,0,0,.35)",
              fontWeight: 700,
            }}
          >
            {securityNotice}
          </div>
        )}
        {error && <div className="viewer-error">{error}</div>}
        <div
          className={`viewer-body ${marking ? "has-marking" : ""} ${pagesCollapsed ? "pages-collapsed" : ""}`}
          style={
            marking
              ? {
                  gridTemplateColumns: `${pagesCollapsed ? 64 : 210}px minmax(0, 1fr) ${dashboardWidth}px`,
                }
              : undefined
          }
        >
          <aside className="thumbnail-rail">
            <header>
              <strong>Pages</strong>
              <button
                type="button"
                className="pages-toggle"
                onClick={() => setPagesCollapsed((value) => !value)}
                aria-expanded={!pagesCollapsed}
                title={pagesCollapsed ? "Expand pages" : "Collapse pages"}
              >
                <Menu />
              </button>
            </header>
            {manifest.pages.map((item) => {
              const visited =
                item.page_number !== currentPage && visitedPages.has(item.page_number);
              return (
                <button
                  className={`${item.page_number === currentPage ? "active" : visited ? "visited" : ""}`}
                  key={item.page_number}
                  onClick={() => goTo(item.page_number)}
                >
                  <span>{item.page_number}</span>
                  <i />{" "}
                  <small>
                    {item.page_number === currentPage
                      ? "Current"
                      : visited
                        ? "Visited"
                        : "Not Visited"}
                  </small>
                </button>
              );
            })}
            <div className="page-status">
              <strong>Page Status</strong>
              <span>
                <i className="visited" />
                Visited ({[...visitedPages].filter((page) => page !== currentPage).length})
              </span>
              <span>
                <i className="current" />
                Current (1)
              </span>
              <span>
                <Flag />
                Flagged ({flaggedQuestions.size})
              </span>
              <span>
                <i />
                Not Visited ({manifest.pages.length - visitedPages.size})
              </span>
            </div>
          </aside>
<<<<<<< HEAD
          <main
            ref={documentStageRef}
            className={`document-stage ${multiPage ? "multi" : "single"}`}
          >
=======
          <section className="document-viewer">
>>>>>>> dba17c5 (Updated project changes)
            <header className="script-header">
              <div>
                <FileText />
                <strong>Answer Script</strong>
              </div>
            </header>
<<<<<<< HEAD
            <div
              className="document-stage-content"
              style={{ width: fit === "custom" ? `${zoom}%` : "100%" }}
            >
            {viewerLoading ? (
              <div className="viewer-empty">
                <RefreshCw className="spin" />
                Loading protected pages
              </div>
            ) : !manifest.pages.length ? (
              <div className="viewer-empty">
                <Eye />
                <strong>No evaluation copy is available</strong>
                <span>
                  Complete anonymization and repository verification first.
                </span>
              </div>
            ) : multiPage ? (
              manifest.pages.map((item) => (
                <img
                  className={enhance ? "enhanced" : ""}
                  key={item.page_number}
                  src={item.url}
                  alt={`Anonymous script page ${item.page_number}`}
                  loading={item.page_number <= 2 ? "eager" : "lazy"}
                  style={{
                    width: "100%",
                    transform: `rotate(${rotation}deg)`,
                  }}
                />
              ))
            ) : page ? (
              <div
                className={`script-page-wrap fit-${fit} ${annotationTool ? "annotating" : ""} ${highlightQuestion ? "question-jump" : ""}`}
                ref={pageRef}
                style={{
                  width: fit === "custom" ? "100%" : undefined,
                  transform: `rotate(${rotation}deg)`,
                }}
                onClick={addAnnotation}
                onPointerDown={startAnnotationDrag}
                onPointerMove={(event) => {
                  updateAnnotationDrag(event);
                  updateAnnotationResize(event);
                }}
                onPointerUp={(event) => {
                  void finishAnnotationDrag(event);
                  void finishAnnotationResize(event);
                }}
                onPointerCancel={(event) => {
                  setAnnotationDrag(null);
                  void finishAnnotationResize(event);
                }}
              >
                <img
                  className={enhance ? "enhanced" : ""}
                  src={page.url}
                  alt={`Anonymous script page ${page.page_number}`}
                  draggable={false}
                />
                {pageAnnotations.map((item) => (
                  <AnnotationLayer
                    item={item}
                    selected={item.id === selectedAnnotation}
                    onSelect={setSelectedAnnotation}
                    geometryOverride={
                      annotationResize?.id === item.id
                        ? annotationResize.current
                        : undefined
                    }
                    onResizeStart={startAnnotationResize}
                    key={item.id}
                  />
                ))}
                {annotationDrag && (
                  <span
                    className={`annotation-shape ${annotationDrag.kind} preview`}
                    style={geometryStyle(
                      geometryFromDrag(
                        annotationDrag.kind,
                        annotationDrag.startX,
                        annotationDrag.startY,
                        annotationDrag.currentX,
                        annotationDrag.currentY,
                      ),
                    )}
                  />
                )}
              </div>
            ) : (
              <div className="viewer-empty">
                Page {currentPage} is unavailable.
              </div>
            )}
            </div>
          </main>
=======
            <main
              ref={documentStageRef}
              className={`document-stage ${multiPage ? "multi" : "single"}`}
              onPointerDown={beginDocumentPan}
              onPointerMove={moveDocumentPan}
              onPointerUp={endDocumentPan}
              onPointerCancel={endDocumentPan}
            >
              {viewerLoading ? (
                <div className="viewer-empty">
                  <RefreshCw className="spin" />
                  Loading protected pages
                </div>
              ) : !manifest.pages.length ? (
                <div className="viewer-empty">
                  <Eye />
                  <strong>No evaluation copy is available</strong>
                  <span>
                    Complete anonymization and repository verification first.
                  </span>
                </div>
              ) : multiPage ? (
                manifest.pages.map((item) => (
                  <img
                    className={enhance ? "enhanced" : ""}
                    key={item.page_number}
                    src={item.url}
                    alt={`Anonymous script page ${item.page_number}`}
                    loading={item.page_number <= 2 ? "eager" : "lazy"}
                    style={{
                      width: `${zoom}%`,
                      transform: `rotate(${rotation}deg)`,
                    }}
                  />
                ))
              ) : page ? (
                <div
                  className={`script-page-wrap fit-${fit} ${annotationTool ? "annotating" : ""} ${highlightQuestion ? "question-jump" : ""}`}
                  style={{
                    width: fit === "custom" ? `${zoom}%` : undefined,
                    transform: `rotate(${rotation}deg)`,
                  }}
                  onClick={addAnnotation}
                >
                  <img
                    className={enhance ? "enhanced" : ""}
                    src={page.url}
                    alt={`Anonymous script page ${page.page_number}`}
                    draggable={false}
                  />
                  {pageAnnotations.map((item) => (
                    <AnnotationLayer
                      item={item}
                      selected={item.id === selectedAnnotation}
                      onSelect={setSelectedAnnotation}
                      key={item.id}
                    />
                  ))}
                </div>
              ) : (
                <div className="viewer-empty">
                  Page {currentPage} is unavailable.
                </div>
              )}
            </main>
          </section>
>>>>>>> dba17c5 (Updated project changes)
          {marking && (
            <MarkingPanel
              marking={marking}
              currentQuestion={currentQuestion}
              setCurrentQuestion={selectQuestion}
              lockToken={lockToken}
              currentPage={currentPage}
              busy={saving || security.paused}
              annotationTool={annotationTool}
              setAnnotationTool={setAnnotationTool}
              onReload={() => refreshMarking(manifest.assignment.id)}
              onUndo={undoAnnotation}
              onSubmit={submitEvaluation}
              setError={setError}
              onQuestionFlagChange={syncQuestionFlagToAssignment}
              dashboardWidth={dashboardWidth}
              onResizeStart={startDashboardResize}
            />
          )}
        </div>
        {aiPanel && aiAssist && (
          <AIAssistPanel
            state={aiAssist}
            busy={saving}
            onAnalyze={requestAIAnalysis}
            onClose={() => setAIPanel(false)}
          />
        )}
        <footer className="evaluation-bottom-bar">
          <button
            className="secondary-button"
            onClick={() => void saveProgress(currentPage)}
          >
            <Save />
            Save Draft
          </button>
          <div className="sync-chip">
            <Check />
            <strong>{syncLabel}</strong>
            <span>
              {syncStatus === "queued"
                ? "Offline changes queued"
                : syncStatus === "syncing"
                  ? "Synchronizing changes"
                  : "All changes are up to date"}
            </span>
          </div>
          <button
            className="secondary-button"
            onClick={() => security.pause("manual_pause")}
            disabled={manifest.assignment.status === "submitted"}
          >
            <Pause />
            Pause Evaluation
          </button>
          <button
<<<<<<< HEAD
  className="viewer-submit"
  onClick={submitEvaluation}
  disabled={
    !marking ||
    saving ||
    security.paused ||
    visitedPages.size < manifest.pages.length ||
    marking.questions
      .filter((item) => item.required)
      .some((item) => !marking.marks.some((mark) => mark.question_id === item.id))
  }
>
=======
            className="viewer-submit"
            onClick={submitEvaluation}
            disabled={
              !marking ||
              saving ||
              security.paused ||
              (security.policy?.camera_required &&
                security.monitoringStatus.camera !== "ok")
            }
          >
>>>>>>> dba17c5 (Updated project changes)
            <SquareCheckBig />
            Submit valuation
          </button>
        </footer>
      </div>
    );
  return (
    <div className="config-workspace">
      {pendingAssignment &&
      identityReadyAssignmentId !== pendingAssignment.id ? (
        <IdentityVerificationModal
          mode="verify"
          assignment={pendingAssignment}
          onClose={cancelPreflight}
          onComplete={() => setIdentityReadyAssignmentId(pendingAssignment.id)}
        />
      ) : pendingAssignment ? (
        <SecurePreflightDialog
          script={pendingAssignment.script}
          assignmentId={pendingAssignment.id}
          controller={security}
          onStart={startSecureEvaluation}
          onCancel={cancelPreflight}
        />
      ) : null}
      <div className="receiving-summary">
        <div>
          <ListFilter />
          <span>Assigned scripts</span>
          <strong>{assignments.length}</strong>
        </div>
        <div>
          <Eye />
          <span>In progress</span>
          <strong>
            {assignments.filter((item) => item.status === "in_progress").length}
          </strong>
        </div>
        <div>
          <Check />
          <span>Completed</span>
          <strong>
            {assignments.filter((item) => item.status === "submitted").length}
          </strong>
        </div>
      </div>
      <div className="workspace-toolbar">
        <div className="entity-tabs evaluation-tabs">
          {filters.map((item) => (
            <button
              className={filter === item.key ? "active" : ""}
              onClick={() => setFilter(item.key)}
              key={item.key}
            >
              {item.label}
            </button>
          ))}
        </div>
        {assignments.some(
          (item) =>
            item.last_opened_at && item.status !== "submitted" && !item.locked,
        ) && (
          <button
            className="primary-button"
            onClick={() => {
              const resume = assignments
                .filter(
                  (item) =>
                    item.last_opened_at &&
                    item.status !== "submitted" &&
                    !item.locked,
                )
                .sort((a, b) =>
                  String(b.last_opened_at).localeCompare(
                    String(a.last_opened_at),
                  ),
                )[0];
              if (resume) void openAssignment(resume);
            }}
          >
            <Eye />
            Resume last script
          </button>
        )}
      </div>
      {notice && (
        <div className="success-banner">
          <Check />
          {notice}
        </div>
      )}
      {error && <div className="form-error">{error}</div>}
      <section className="panel">
        {loading ? (
          <div className="empty-state">
            <RefreshCw className="spin" />
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Anonymous script</th>
                  <th>Paper</th>
                  <th>Round</th>
                  <th>Progress</th>
                  <th>Due</th>
                  <th>State</th>
                  <th>Controls</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <strong>{item.script}</strong>
                      <br />
                      <small>
                        {item.page_count} pages · P{item.priority}
                      </small>
                    </td>
                    <td>{item.paper}</td>
                    <td>{item.valuation_round}</td>
                    <td>
                      <div className="desk-progress">
                        <div>
                          <span
                            style={{ width: `${item.progress_percent}%` }}
                          />
                        </div>
                        <small>
                          {item.progress_percent}%
                          {item.draft_saved_at && item.status !== "submitted" ? " · Draft saved" : ""}
                        </small>
                      </div>
                    </td>
                    <td>{new Date(item.due_at).toLocaleString("en-IN")}</td>
                    <td>
                      <span
                        className={`status-pill ${item.locked ? "attention" : item.status}`}
                      >
                        {item.locked
                          ? "In another session"
                          : titleCase(item.status)}
                      </span>
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          title={
                            item.locked
                              ? "Evaluation is open in another session"
                              : "Open secure evaluation"
                          }
                          disabled={item.locked}
                          onClick={() => openAssignment(item)}
                        >
                          <Eye />
                        </button>
                        <button
                          type="button"
                          title={item.is_flagged ? "Unflag script" : "Flag script for review"}
                          aria-label={item.is_flagged ? "Unflag script" : "Flag script for review"}
                          aria-pressed={item.is_flagged}
                          className={item.is_flagged ? "flagged" : undefined}
                          disabled={flaggingAssignmentId === item.id}
                          onClick={() => void toggleAssignmentFlag(item)}
                        >
                          <Flag fill={item.is_flagged ? "currentColor" : "none"} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!loading && !visible.length && (
          <div className="empty-state">
            No scripts match this evaluation queue.
          </div>
        )}
      </section>
    </div>
  );
}


function AIAssistPanel({
  state,
  busy,
  onAnalyze,
  onClose,
}: {
  state: AIAssist;
  busy: boolean;
  onAnalyze: () => Promise<void>;
  onClose: () => void;
}) {
  const analysis = state.analysis;
  const processing =
    analysis && ["queued", "running"].includes(analysis.status);
  const total =
    analysis?.assessments.reduce((sum, item) => sum + item.marks, 0) || 0;

  return (
    <aside className="ai-assist-panel" aria-label="AI analysis suggestions">
      <header>
        <div>
          <Sparkles />
          <span>
            <strong>AI analysis</strong>
            <small>Advisory only. You remain the evaluator.</small>
          </span>
        </div>
        <button
          className="icon-button"
          title="Close AI analysis"
          onClick={onClose}
        >
          <X />
        </button>
      </header>
      {!analysis ? (
        <div className="ai-assist-empty">
          <Sparkles />
          <strong>Analyze this anonymous script</strong>
          <p>
            ADMIEZO AI Assistant will compare the masked paper with the
            configured question text and marking guidance. Suggested marks
            are never applied automatically.
          </p>
          <button
            className="viewer-primary"
            onClick={() => void onAnalyze()}
            disabled={busy || !state.provider.configured}
          >
            <Sparkles />
            Analyze with ADMIEZO AI
          </button>
          {!state.provider.configured && (
            <small>
              ADMIEZO AI Assistant is not configured by the platform
              administrator.
            </small>
          )}
        </div>
      ) : processing ? (
        <div className="ai-assist-empty">
          <RefreshCw className="spin" />
          <strong>
            {analysis.status === "queued"
              ? "Waiting for ADMIEZO AI Assistant"
              : "Analyzing answer paper"}
          </strong>
          <p>You can continue evaluating while this runs.</p>
        </div>
      ) : analysis.status === "failed" ? (
        <div className="ai-assist-empty error">
          <ShieldAlert />
          <strong>Analysis failed</strong>
          <p>
            {analysis.error_message ||
              "ADMIEZO AI Assistant could not complete this analysis."}
          </p>
          <button
            className="viewer-secondary"
            onClick={() => void onAnalyze()}
            disabled={busy}
          >
            Try again
          </button>
        </div>
      ) : (
        <div className="ai-assist-complete">
          <div className="ai-assist-summary">
            <div>
              <span>Suggested total</span>
              <strong>{total}</strong>
            </div>
            <div>
              <span>Confidence</span>
              <strong>{analysis.effective_confidence ?? 0}%</strong>
            </div>
          </div>
          {analysis.summary && (
            <p className="ai-assist-overview">{analysis.summary}</p>
          )}
          <div className="ai-assist-results">
            {analysis.assessments.map((item) => (
              <article key={item.question_id}>
                <header>
                  <strong>Question {item.question}</strong>
                  <span>
                    {item.marks} marks · {item.confidence}%
                  </span>
                </header>
                <p>{item.feedback || item.reasoning}</p>
                {item.feedback && item.reasoning && (
                  <small>{item.reasoning}</small>
                )}
              </article>
            ))}
          </div>
          <footer>
            <span>Review each suggestion against the script.</span>
            <button
              className="viewer-secondary"
              onClick={() => void onAnalyze()}
              disabled={busy}
            >
              <RefreshCw />
              Analyze again
            </button>
          </footer>
        </div>
      )}
    </aside>
  );
}

function geometryStyle(geometry: {
  x: number;
  y: number;
  width: number;
  height: number;
}) {
  return {
    left: `${geometry.x * 100}%`,
    top: `${geometry.y * 100}%`,
    width: `${geometry.width * 100}%`,
    height: `${geometry.height * 100}%`,
  };

}

function AnnotationLayer({
  item,
  selected,
  onSelect,
  geometryOverride,
  onResizeStart,
}: {
  item: Annotation;
  selected: boolean;
  onSelect: (id: string) => void;
  geometryOverride?: { x: number; y: number; width: number; height: number };
  onResizeStart: (
    id: string,
    handle: ResizeHandle,
    event: ReactPointerEvent<HTMLSpanElement>,
  ) => void;
}) {
  const geometry = geometryOverride || {
    x: Number(item.geometry.x || 0),
    y: Number(item.geometry.y || 0),
    width: Number(item.geometry.width || 0.04),
    height: Number(item.geometry.height || 0.04),
  };
  const select = (event: MouseEvent<HTMLElement>) => {
    event.stopPropagation();
    onSelect(item.id);
  };
  if (item.kind === "tick" || item.kind === "cross")
    return (
      <span
        className={`annotation-mark ${item.kind} ${selected ? "selected" : ""}`}
        style={{ left: `${geometry.x * 100}%`, top: `${geometry.y * 100}%` }}
        onClick={select}
      >
        {item.kind === "tick" ? "✓" : "×"}
      </span>
    );
  if (item.kind === "arrow") {
    const points = item.geometry.points as { x: number; y: number }[];
    const end = points?.at(-1) || { x: geometry.x, y: geometry.y };
    return (
      <span
        className={`annotation-mark arrow ${selected ? "selected" : ""}`}
        style={{ left: `${end.x * 100}%`, top: `${end.y * 100}%` }}
        onClick={select}
      >
        ➜
      </span>
    );
  }
  const handles: ResizeHandle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
  return (
    <span
      className={`annotation-shape ${item.kind} ${selected ? "selected" : ""}`}
      style={geometryStyle(geometry)}
      onClick={select}
    >
      {selected && ["underline", "highlight", "circle", "rectangle"].includes(item.kind) &&
        handles.map((handle) => (
          <span
            key={handle}
            className={`annotation-handle ${handle}`}
            onPointerDown={(event) => onResizeStart(item.id, handle, event)}
          />
        ))}
    </span>
  );
}

function MarkingPanel({
  marking,
  currentQuestion,
  setCurrentQuestion,
  lockToken,
  currentPage,
  busy,
  annotationTool,
  setAnnotationTool,
  onReload,
  onUndo,
  onSubmit,
  setError,
  onQuestionFlagChange,
  dashboardWidth,
  onResizeStart,
}: {
  marking: Marking;
  currentQuestion: string;
  setCurrentQuestion: (id: string) => void;
  lockToken: string;
  currentPage: number;
  busy: boolean;
  annotationTool: AnnotationKind | null;
  setAnnotationTool: (kind: AnnotationKind | null) => void;
  onReload: () => Promise<Marking>;
  onUndo: () => Promise<void>;
  onSubmit: () => Promise<void>;
  setError: (message: string) => void;
  onQuestionFlagChange: (flagged: boolean) => Promise<void>;
  dashboardWidth: number;
  onResizeStart: (event: React.PointerEvent) => void;
}) {
  const question =
    marking.questions.find((item) => item.id === currentQuestion) ||
    marking.questions[0];
  const existing = marking.marks.find(
    (item) => item.question_id === question?.id,
  );
  const [marks, setMarks] = useState(String(existing?.marks ?? 0));
  const [outcome, setOutcome] = useState(existing?.outcome || "evaluated");
  const [adjustment, setAdjustment] = useState(existing?.adjustment || "none");
  const [reviewByQuestion, setReviewByQuestion] = useState<Record<string, boolean>>({});
  const [confirmed, setConfirmed] = useState(
    existing?.examiner_confirmed || false,
  );
  const [comments, setComments] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<"marking" | "paper" | "scheme" | "sample">("marking");
  const currentQuestionIndex = question
    ? marking.questions.findIndex((item) => item.id === question.id)
    : -1;
  const previousQuestion =
    currentQuestionIndex > 0
      ? marking.questions[currentQuestionIndex - 1]
      : null;
  const nextQuestion =
    currentQuestionIndex >= 0 &&
    currentQuestionIndex < marking.questions.length - 1
      ? marking.questions[currentQuestionIndex + 1]
      : null;
  const comment = question?.id ? (comments[question.id] ?? "") : "";
  const marksPattern = /^\d*\.?\d?$/;
  const validateMarks = (value: string) => {
    if (!value || value === ".") {
      setError("Marks awarded must be a number");
      return null;
    }
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0 || (question?.max_marks !== undefined && parsed > question.max_marks)) {
      setError(`Marks awarded must be between 0 and ${question?.max_marks ?? 0}`);
      return null;
    }
    return parsed;
  };
  useEffect(() => {
    const mark = marking.marks.find(
      (item) => item.question_id === question?.id,
    );
    setMarks(String(mark?.marks ?? 0));
    setOutcome(mark?.outcome || "evaluated");
    setAdjustment(mark?.adjustment || "none");
    if (question?.id) {
      setReviewByQuestion((previous) => ({
        ...previous,
        [question.id]: Boolean(mark?.marked_for_review),
      }));
    }
    setConfirmed(mark?.examiner_confirmed || false);
  }, [marking.marks, question?.id]);
  async function saveMark(event: FormEvent) {
    event.preventDefault();
    if (!question) return;
    const parsedMarks = validateMarks(marks);
    if (parsedMarks === null) return;
    const review = Boolean(reviewByQuestion[question.id]);
    try {
      await api(`/api/v1/marking/evaluations/${marking.evaluation.id}/marks`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Assignment-Lock": lockToken,
        },
        body: JSON.stringify({
          version: marking.evaluation.version,
          question_id: question.id,
          marks: parsedMarks,
          outcome,
          adjustment,
          marked_for_review: review,
          requires_attention: adjustment !== "none",
          examiner_confirmed: confirmed,
          rubric_criterion_id: question.criteria[0]?.id || null,
        }),
      });
      await onReload();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Mark could not be saved",
      );
    }
  }
  async function saveComment() {
    if (!question || !comment.trim()) return;
    try {
      await api(
        `/api/v1/marking/evaluations/${marking.evaluation.id}/comments`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Assignment-Lock": lockToken,
          },
          body: JSON.stringify({
            version: marking.evaluation.version,
            question_id: question.id,
            kind: "question",
            body: comment,
          }),
        },
      );
      setComments((previous) => ({ ...previous, [question.id]: "" }));
      await onReload();
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Comment could not be saved",
      );
    }
  }
  async function toggleQuestionFlag() {
    if (!question) return;
    const parsedMarks = validateMarks(marks);
    if (parsedMarks === null) return;
    const review = question.id ? Boolean(reviewByQuestion[question.id]) : false;
    const nextReview = !review;
    let updatedMarking: Marking | null = null;
    try {
      await api(`/api/v1/marking/evaluations/${marking.evaluation.id}/marks`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Assignment-Lock": lockToken,
        },
        body: JSON.stringify({
          version: marking.evaluation.version,
          question_id: question.id,
          marks: parsedMarks,
          outcome,
          adjustment,
          marked_for_review: nextReview,
          requires_attention: adjustment !== "none",
          examiner_confirmed: confirmed,
          rubric_criterion_id: question.criteria[0]?.id || null,
        }),
      });
      updatedMarking = await onReload();
      await onQuestionFlagChange(
        updatedMarking.marks.some((mark) => mark.marked_for_review),
      );
      setReviewByQuestion((previous) => ({
        ...previous,
        [question.id]: nextReview,
      }));
    } catch (reason) {
      if (updatedMarking) {
        try {
          await api(`/api/v1/marking/evaluations/${marking.evaluation.id}/marks`, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-Assignment-Lock": lockToken,
            },
            body: JSON.stringify({
              version: updatedMarking.evaluation.version,
              question_id: question.id,
              marks: parsedMarks,
              outcome,
              adjustment,
              marked_for_review: review,
              requires_attention: adjustment !== "none",
              examiner_confirmed: confirmed,
              rubric_criterion_id: question.criteria[0]?.id || null,
            }),
          });
          await onReload();
        } catch {
          /* Preserve the original error while the next refresh reconciles state. */
        }
      }
      setReviewByQuestion((previous) => ({
        ...previous,
        [question.id]: review,
      }));
      setError(
        reason instanceof Error
          ? reason.message
          : "Question flag could not be saved",
      );
    }
  }
  const complete = new Set(marking.marks.map((item) => item.question_id));
  const requiredQuestions = marking.questions.filter((item) => item.required);
  const required = requiredQuestions.length;
  const completedRequired = requiredQuestions.filter((item) => complete.has(item.id)).length;
  const readOnly =
    marking.evaluation.status === "submitted" ||
    marking.evaluation.status === "locked";
  return (
    <aside
      className="marking-panel evaluation-dashboard-panel"
      style={{ width: dashboardWidth }}
    >
      <div
        className="dashboard-resize-handle"
        onPointerDown={onResizeStart}
        role="separator"
        aria-orientation="vertical"
        title="Resize evaluation dashboard"
      />
      <header>
        <div>
          <strong>Evaluation Dashboard</strong>
          <span>
            v{marking.scheme.version} · {titleCase(marking.evaluation.status)}
          </span>
        </div>
        <div className="mark-total">
          <span>Total</span>
          <strong>{marking.evaluation.total_marks}</strong>
        </div>
      </header>
      <div className="dashboard-tabs">
        <button
          className={tab === "marking" ? "active" : ""}
          onClick={() => setTab("marking")}
        >
          Marking
        </button>
        <button
          className={tab === "paper" ? "active" : ""}
          onClick={() => setTab("paper")}
        >
          Question Paper
        </button>
        <button
          className={tab === "scheme" ? "active" : ""}
          onClick={() => setTab("scheme")}
        >
          Scheme of Evaluation
        </button>
        <button className={tab === "sample" ? "active" : ""} onClick={() => setTab("sample")}>
    Sample Answer Paper ▾
  </button>
      </div>
      {tab === "marking" && (
        <>
          <div className="question-strip">
            {marking.questions.map((item) => {
              const anchor = marking.page_anchors.find(
                (pageAnchor) => pageAnchor.question_id === item.id,
              );
              const flagged = marking.marks.some(
                (mark) =>
                  mark.question_id === item.id && mark.marked_for_review,
              );
              return (
                <button
                  className={`${item.id === question?.id ? "active" : ""}${anchor ? " pinned" : ""}${flagged ? " flagged" : ""}`}
                  title={
                    anchor
                      ? `Pinned to page ${anchor.page_number}`
                      : `Question ${item.number}`
                  }
                  onClick={() => setCurrentQuestion(item.id)}
                  key={item.id}
                >
                  <span>{item.number}</span>
                  {complete.has(item.id) && <Check />}
                </button>
              );
            })}
          </div>
          <div className="marking-scroll">
            <section className="marking-section">
              <div className="marking-section-title question-nav-title">
                <button
                  type="button"
                  className="question-arrow"
                  onClick={() =>
                    previousQuestion && setCurrentQuestion(previousQuestion.id)
                  }
                  disabled={!previousQuestion}
                  title="Previous question"
                >
                  <ChevronLeft />
                </button>
                <div className="question-title">
                  <strong>{question?.number}</strong>
                </div>
                <button
                  type="button"
                  className="question-arrow"
                  onClick={() =>
                    nextQuestion && setCurrentQuestion(nextQuestion.id)
                  }
                  disabled={!nextQuestion}
                  title="Next question"
                >
                  <ChevronRight />
                </button>
                <span>Maximum {question?.max_marks}</span>
              </div>
              {question?.criteria.map((item) => (
                <div className="criterion-note" key={item.id}>
                  <strong>
                    {item.code} · {item.max_marks}
                  </strong>
                  <span>{item.description}</span>
                </div>
              ))}
              <form className="mark-form" onSubmit={saveMark}>
                <label>
                  <span>Marks awarded</span>
                  <input
                    type="text"
                    inputMode="decimal"
                    min={0}
                    max={question?.max_marks}
                    value={marks}
                    onChange={(event) => {
                      if (marksPattern.test(event.target.value)) setMarks(event.target.value);
                    }}
                    onBlur={() => {
                      if (marks) validateMarks(marks);
                    }}
                    disabled={readOnly}
                  />
                </label>
                <label>
                  <span>Outcome</span>
                  <select
                    value={outcome}
                    onChange={(event) => {
                      setOutcome(event.target.value);
                      if (event.target.value !== "evaluated") setMarks("0");
                    }}
                    disabled={readOnly}
                  >
                    <option value="evaluated">Evaluated</option>
                    <option value="unanswered">Unanswered</option>
                  </select>
                </label>
                <label>
                  <span>Adjustment</span>
                  <select
                    value={adjustment}
                    onChange={(event) => setAdjustment(event.target.value)}
                    disabled={readOnly}
                  >
                    <option value="none">None</option>
                    <option value="grace">Grace</option>
                  </select>
                </label>
                <div className="mark-checks">
                  {adjustment !== "none" && (
                    <label>
                      <input
                        type="checkbox"
                        checked={confirmed}
                        onChange={(event) => setConfirmed(event.target.checked)}
                        disabled={readOnly}
                      />
                      Confirm exception
                    </label>
                  )}
                </div>
                {!readOnly && (
                  <button className="viewer-primary" disabled={busy}>
                    <Save />
                    Save mark
                  </button>
                )}
              </form>
            </section>
            <section className="marking-section">
              <div className="marking-section-title">
                <strong>Annotations</strong>
                <span>Click and drag the script to draw</span>
              </div>
              <div className="annotation-tools">
                {tools.map((tool) => (
                  <button
                    title={tool.label}
                    className={annotationTool === tool.kind ? "active" : ""}
                    onClick={() =>
                      setAnnotationTool(
                        annotationTool === tool.kind ? null : tool.kind,
                      )
                    }
                    disabled={readOnly}
                    key={tool.kind}
                  >
                    <tool.icon />
                  </button>
                ))}
                <button
                  title="Undo last annotation"
                  onClick={onUndo}
                  disabled={readOnly || !marking.annotations.length}
                >
                  <Undo2 />
                </button>
              </div>
            </section>
            <section className="marking-section">
              <button
                type="button"
                className={`viewer-secondary question-flag-action ${question?.id && reviewByQuestion[question.id] ? "active" : ""}`}
                onClick={toggleQuestionFlag}
                disabled={readOnly || busy}
              >
                <Flag />
                {question?.id && reviewByQuestion[question.id]
                  ? "Unflag Question"
                  : "⚑ Flag Question"}
              </button>
            </section>
            <section className="marking-section">
              <div className="marking-section-title">
                <strong>Question comment</strong>
                <MessageSquareText />
              </div>
              <textarea
                value={comment}
                onChange={(event) =>
                  question?.id &&
                  setComments((previous) => ({
                    ...previous,
                    [question.id]: event.target.value,
                  }))
                }
                placeholder="Record an examiner comment"
                disabled={readOnly}
              />
              {!readOnly && (
                <button
                  className="viewer-secondary"
                  onClick={saveComment}
                  disabled={!comment.trim()}
                >
                  <MessageSquareText />
                  Add comment
                </button>
              )}
              <div className="comment-list">
                {marking.comments
                  .filter((item) => item.question_id === question?.id)
                  .map((item) => (
                    <p key={item.id}>{item.body}</p>
                  ))}
              </div>
            </section>
          </div>
          <footer>
            <span>
              {completedRequired}/{required} questions evaluated
            </span>
            {marking.evaluation.status === "submitted" && (
              <button
                className="viewer-submit"
                onClick={onSubmit}
                disabled={busy}
              >
                <SquareCheckBig />
                Finalize valuation
              </button>
            )}
          </footer>
        </>
      )}
      {tab === "paper" && (
        <div className="dashboard-document">
          <h3>{marking.scheme.title}</h3>
          {marking.questions.map((item) => (
            <article key={item.id}>
              <strong>{item.number}</strong>
              <span>{item.max_marks} marks</span>
              {item.criteria.map((criterion) => (
                <p key={criterion.id}>{criterion.description}</p>
              ))}
            </article>
          ))}
        </div>
      )}
      {tab === "scheme" && (
        <div className="dashboard-document">
          <h3>{marking.scheme.title}</h3>
          <section>
            <strong>Evaluation guidelines</strong>
            <p>{marking.scheme.guidelines}</p>
          </section>
          <section>
            <strong>Examiner instructions</strong>
            <p>{marking.scheme.instructions}</p>
          </section>
        </div>
      )}
      {tab === "sample" && (
        <div className="dashboard-document">
          <div className="empty-state">
            <p>No sample papers available</p>
          </div>
        </div>
      )}
    </aside>
  );
}
