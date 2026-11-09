// Notifications (owner P08): bell with rose dot + count for screen readers, popover 320 px, "Marcar todo como leído".
// Source: P07 GET /api/v1/workflow/notifications (company-scoped, policy-filtered); marking read uses the
// per-item action POST …/{id}:mark-read with an Idempotency-Key (bulk endpoint requested in CCR P08-1).
import * as RP from "@radix-ui/react-popover";
import { Bell } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { useT } from "@i18n/index";
import { formatInstant } from "@lib/dates";
import type { NotificationItem, Page } from "@lib/api/types";
import { useToast } from "@ds/components/Toast";
import { ApiError } from "@lib/api/client";
import { useResource, useShell } from "./ShellContext";

export function NotificationsPopover({ endpoint = "/api/v1/workflow/notifications" }: { endpoint?: string }) {
  const t = useT();
  const shell = useShell();
  const toast = useToast();
  const res = useResource<Page<NotificationItem>>(endpoint, { company_id: shell.companyId });
  const [busy, setBusy] = useState(false);
  const items = res.data?.results ?? [];

  async function markAll() {
    setBusy(true);
    try {
      for (const n of items.filter((x) => !x.read)) {
        await shell.api.post(`${endpoint}/${encodeURIComponent(n.id)}:mark-read`, { params: { company_id: shell.companyId } });
      }
      res.reload();
    } catch (e) {
      toast.push({ tone: "error", title: t("notifications.error"), traceId: e instanceof ApiError ? e.traceId : null });
    } finally {
      setBusy(false);
    }
  }
  return <NotificationsView items={items} loading={res.loading} error={Boolean(res.error)} busy={busy} onMarkAll={() => void markAll()} />;
}

export function NotificationsView({ items, loading, error, busy, onMarkAll }: { items: NotificationItem[]; loading: boolean; error: boolean; busy: boolean; onMarkAll: () => void }) {
  const t = useT();
  const unread = items.filter((n) => !n.read).length;

  return (
    <RP.Root>
      <RP.Trigger asChild>
        <button
          type="button"
          aria-label={`${t("header.notifications")}: ${t("header.unread", { count: unread })}`}
          className="relative inline-flex min-h-8 min-w-8 items-center justify-center rounded-control p-2 text-secondary hover:bg-nav-hover"
          data-nx-control="notifications"
        >
          <Bell aria-hidden="true" size={16} />
          {unread > 0 ? <span aria-hidden="true" className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-dot" /> : null}
        </button>
      </RP.Trigger>
      <RP.Portal>
        <RP.Content align="end" sideOffset={8} className="z-[50] w-80 rounded-card border border-line bg-surface p-0 shadow-xl" aria-label={t("notifications.title")}>
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 className="text-sm font-bold text-fg">{t("notifications.title")}</h2>
            <button type="button" disabled={busy || unread === 0} onClick={onMarkAll} className="min-h-6 text-xs font-semibold text-brand disabled:text-muted">
              {t("notifications.markAll")}
            </button>
          </div>
          <div className="max-h-96 overflow-y-auto">
            {error ? (
              <p className="px-4 py-6 text-sm text-danger">{t("notifications.error")}</p>
            ) : items.length === 0 ? (
              <p className="px-4 py-6 text-sm text-muted">{loading ? t("app.loading") : t("notifications.empty")}</p>
            ) : (
              <ul className="divide-y divide-line-subtle">
                {items.map((n) => (
                  <li key={n.id} className="px-4 py-3">
                    {n.href ? (
                      <Link to={n.href} className="text-sm font-medium text-fg hover:underline">
                        {n.title}
                      </Link>
                    ) : (
                      <p className="text-sm font-medium text-fg">{n.title}</p>
                    )}
                    {n.body ? <p className="text-xs text-secondary">{n.body}</p> : null}
                    <p className="text-xs text-muted">
                      {formatInstant(n.created_at)}
                      {!n.read ? <span className="ml-2 font-semibold text-brand">•</span> : null}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </RP.Content>
      </RP.Portal>
    </RP.Root>
  );
}
