"use client";

import { Bell, Check, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { csrfFetch } from "@/lib/api";
import { playNotificationSound } from "@/lib/notification-sound";

type Notification = { id: string; category: string; title: string; body: string; severity: string; status: string; created_at: string };

function seenNotificationIds() {
  try {
    const stored = JSON.parse(sessionStorage.getItem("admiezo-seen-notifications") || "[]");
    return new Set<string>(Array.isArray(stored) ? stored.filter((item): item is string => typeof item === "string") : []);
  } catch {
    return new Set<string>();
  }
}

export function NotificationCenter({ onOpenEvaluations, onOpenLiveOperations, onOpenAllocation }: { onOpenEvaluations: () => void; onOpenLiveOperations: () => void; onOpenAllocation: () => void }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [headsUp, setHeadsUp] = useState<Notification | null>(null);

  const load = useCallback(async () => {
    const response = await csrfFetch("/api/v1/auth/notifications");
    if (!response.ok) return;
    const body = await response.json();
    const nextItems = (body.items || []) as Notification[];
    const seenKey = "admiezo-seen-notifications";
    const seen = seenNotificationIds();
    const freshItems = nextItems.filter((item) => item.status !== "acknowledged" && item.category !== "remote_support" && !seen.has(item.id));
    const fresh = freshItems[0];
    if (fresh) {
      setHeadsUp(fresh);
      playNotificationSound(fresh.severity === "high" || fresh.severity === "critical");
      freshItems.forEach((item) => seen.add(item.id));
      sessionStorage.setItem(seenKey, JSON.stringify([...seen].slice(-100)));
    }
    setItems(nextItems); setUnread(body.unread_count || 0);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => void load(), 8000);
    return () => { window.clearTimeout(timer); window.clearInterval(interval); };
  }, [load]);

  async function read(item: Notification) {
    if (item.status !== "acknowledged") await csrfFetch(`/api/v1/auth/notifications/${item.id}/read`, { method: "POST" });
    await load();
    setHeadsUp((current) => current?.id === item.id ? null : current);
    if (item.category === "assignment") { setOpen(false); onOpenEvaluations(); }
    if (item.category === "evaluator_help") { setOpen(false); onOpenLiveOperations(); }
    if (item.category === "allocation_request") { setOpen(false); onOpenAllocation(); }
  }

  return <div className="notification-center">
    <button className={`icon-button ${unread ? "has-unread" : ""}`} title={t("notifications.title")} aria-label={`${t("notifications.title")}${unread ? `, ${t("notifications.unread", { count: unread })}` : ""}`} onClick={() => setOpen(!open)}><Bell />{unread > 0 && <span className="notification-badge">{unread > 99 ? "99+" : unread}</span>}</button>
    {headsUp && !open && <div className={`notification-heads-up ${headsUp.severity}`} role="alert"><button className="notification-heads-up-main" onClick={() => { setOpen(true); setHeadsUp(null); }}><span className={`notification-indicator ${headsUp.severity}`} /><span><strong>{headsUp.title}</strong><small>{headsUp.body}</small></span></button><button className="notification-heads-up-close" title={t("notifications.close")} onClick={() => setHeadsUp(null)}><X /></button></div>}
    {open && <div className="notification-popover"><header><div><strong>{t("notifications.title")}</strong><span>{unread ? t("notifications.unread", { count: unread }) : t("notifications.caughtUp")}</span></div><button className="icon-button" title={t("notifications.close")} onClick={() => setOpen(false)}><X /></button></header><div className="notification-list">{items.map((item) => <button className={item.status === "acknowledged" ? "read" : ""} onClick={() => void read(item)} key={item.id}><span className={`notification-indicator ${item.severity}`} /> <div><strong>{item.title}</strong><p>{item.body}</p><small>{new Date(item.created_at).toLocaleString(i18n.resolvedLanguage === "hi" ? "hi-IN" : i18n.resolvedLanguage === "kn" ? "kn-IN" : "en-IN")}</small></div>{item.status === "acknowledged" && <Check />}</button>)}{!items.length && <div className="compact-empty">{t("notifications.empty")}</div>}</div></div>}
  </div>;
}
