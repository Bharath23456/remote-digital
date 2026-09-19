"use client";

import { Bell, Check, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { csrfFetch } from "@/lib/api";

type Notification = { id: string; category: string; title: string; body: string; severity: string; status: string; created_at: string };

export function NotificationCenter({ onOpenEvaluations }: { onOpenEvaluations: () => void }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);

  const load = useCallback(async () => {
    const response = await csrfFetch("/api/v1/auth/notifications");
    if (!response.ok) return;
    const body = await response.json();
    setItems(body.items || []); setUnread(body.unread_count || 0);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => void load(), 30000);
    return () => { window.clearTimeout(timer); window.clearInterval(interval); };
  }, [load]);

  async function read(item: Notification) {
    if (item.status !== "acknowledged") await csrfFetch(`/api/v1/auth/notifications/${item.id}/read`, { method: "POST" });
    await load();
    if (item.category === "assignment") { setOpen(false); onOpenEvaluations(); }
  }

  return <div className="notification-center">
    <button className="icon-button" title={t("notifications.title")} aria-label={`${t("notifications.title")}${unread ? `, ${t("notifications.unread", { count: unread })}` : ""}`} onClick={() => setOpen(!open)}><Bell />{unread > 0 && <span className="notification-badge">{unread > 9 ? "9+" : unread}</span>}</button>
    {open && <div className="notification-popover"><header><div><strong>{t("notifications.title")}</strong><span>{unread ? t("notifications.unread", { count: unread }) : t("notifications.caughtUp")}</span></div><button className="icon-button" title={t("notifications.close")} onClick={() => setOpen(false)}><X /></button></header><div className="notification-list">{items.map((item) => <button className={item.status === "acknowledged" ? "read" : ""} onClick={() => void read(item)} key={item.id}><span className={`notification-indicator ${item.severity}`} /> <div><strong>{item.title}</strong><p>{item.body}</p><small>{new Date(item.created_at).toLocaleString(i18n.resolvedLanguage === "hi" ? "hi-IN" : i18n.resolvedLanguage === "kn" ? "kn-IN" : "en-IN")}</small></div>{item.status === "acknowledged" && <Check />}</button>)}{!items.length && <div className="compact-empty">{t("notifications.empty")}</div>}</div></div>}
  </div>;
}
