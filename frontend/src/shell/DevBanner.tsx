// DEV banner (owner P08): dev only, non-dismissible, full width, 28 px, amber (UI-CONTRACT §3).
import { useT } from "@i18n/index";

export function DevBanner() {
  const t = useT();
  return (
    <div role="note" data-nx-region="dev-banner" className="flex h-7 shrink-0 items-center justify-center bg-amber-50 px-4 text-xs font-semibold text-amber-700 dark:bg-amber-700/20 dark:text-amber-500">
      <span className="truncate">{t("app.devBanner")}</span>
    </div>
  );
}
