// Feed / list panel (owner P08): recent activity from a provider; honest empty state.
import { Link } from "react-router";
import { useT } from "@i18n/index";
import { formatInstant } from "@lib/dates";
import { EmptyState, Skeleton } from "./Feedback";

export interface FeedItem {
  id: string;
  title: string;
  subtitle?: string;
  at?: string;
  href?: string;
}

export function FeedPanel({ title, items, loading }: { title?: string; items: FeedItem[] | null; loading?: boolean }) {
  const t = useT();
  const heading = title ?? t("feed.title");
  return (
    <section className="nx-card flex flex-col p-6" aria-label={heading}>
      <h3 className="mb-4 text-base font-bold text-fg">{heading}</h3>
      {loading ? (
        <Skeleton className="h-40 w-full" label={t("dashboard.loading")} />
      ) : !items || items.length === 0 ? (
        <EmptyState title={t("feed.empty")} />
      ) : (
        <ul className="flex flex-col divide-y divide-line-subtle">
          {items.map((it) => (
            <li key={it.id} className="py-2">
              {it.href ? (
                <Link to={it.href} className="text-sm font-medium text-fg hover:underline">
                  {it.title}
                </Link>
              ) : (
                <p className="text-sm font-medium text-fg">{it.title}</p>
              )}
              {it.subtitle ? <p className="text-xs text-secondary">{it.subtitle}</p> : null}
              {it.at ? <p className="text-xs text-muted">{formatInstant(it.at)}</p> : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
