// Role home dashboard framework (owner P08): modules register widgets (KPIs, lists, charts); the default layout is per
// role; the user can reorder/hide widgets (buttons — a dragging alternative, WCAG 2.5.7) and the layout persists per user.
import { Suspense, useState, type ComponentType } from "react";
import { ArrowDown, ArrowUp, EyeOff } from "lucide-react";
import { useT } from "@i18n/index";
import { can } from "@lib/permissions";
import { prefKey, readPref, writePref } from "@lib/storage";
import { IconButton } from "@ds/components/Button";
import { Skeleton } from "@ds/components/Feedback";
import { useShell } from "./ShellContext";

export interface HomeWidget {
  id: string;
  title: string;
  permission: string;
  /** Roles whose default layout includes the widget (empty = everyone with the permission). */
  roles?: string[];
  component: ComponentType;
}

const registry = new Map<string, HomeWidget>();

export function registerHomeWidget(w: HomeWidget): () => void {
  registry.set(w.id, w);
  return () => registry.delete(w.id);
}

export function availableWidgets(perms: ReadonlySet<string>, roles: readonly string[]): HomeWidget[] {
  return [...registry.values()].filter((w) => can(perms, w.permission) && (!w.roles?.length || w.roles.some((r) => roles.includes(r))));
}

export function HomeWidgets() {
  const t = useT();
  const shell = useShell();
  const list = availableWidgets(shell.perms, shell.context.roles ?? []);
  const key = prefKey(shell.userId, "home.layout");
  const [layout, setLayout] = useState<{ order: string[]; hidden: string[] }>(() => readPref(key, { order: [], hidden: [] }));
  if (!list.length) return null;
  const ordered = [...list].sort((a, b) => {
    const ia = layout.order.indexOf(a.id);
    const ib = layout.order.indexOf(b.id);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
  });
  const visible = ordered.filter((w) => !layout.hidden.includes(w.id));
  const save = (next: typeof layout) => {
    setLayout(next);
    writePref(key, next);
  };
  const move = (id: string, d: -1 | 1) => {
    const ids = visible.map((w) => w.id);
    const i = ids.indexOf(id);
    const j = i + d;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j] as string, ids[i] as string];
    save({ ...layout, order: ids });
  };
  return (
    <section aria-label={t("home.widgets")} className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {visible.map((w, i) => {
        const W = w.component;
        return (
          <div key={w.id} className="nx-card p-6">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-base font-bold text-fg">{w.title}</h2>
              <div className="flex gap-1" role="group" aria-label={t("home.customize")}>
                <IconButton label={`${w.title}: ↑`} icon={<ArrowUp size={14} />} disabled={i === 0} onClick={() => move(w.id, -1)} />
                <IconButton label={`${w.title}: ↓`} icon={<ArrowDown size={14} />} disabled={i === visible.length - 1} onClick={() => move(w.id, 1)} />
                <IconButton label={`${w.title}: ✕`} icon={<EyeOff size={14} />} onClick={() => save({ ...layout, hidden: [...layout.hidden, w.id] })} />
              </div>
            </div>
            <Suspense fallback={<Skeleton className="h-24 w-full" label={t("app.loading")} />}>
              <W />
            </Suspense>
          </div>
        );
      })}
    </section>
  );
}
