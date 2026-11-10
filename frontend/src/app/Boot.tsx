// Boot states (owner P08): loading and context-error screens shown before the shell has its context.
import { useT } from "@i18n/index";
import { ApiError } from "@lib/api/client";
import { Button } from "@ds/components/Button";

export function BootLoading() {
  const t = useT();
  return (
    <div role="status" className="flex h-screen items-center justify-center bg-app text-sm text-muted">
      {t("app.loading")}
    </div>
  );
}

export function BootError({ error, retry }: { error: unknown; retry: () => void }) {
  const t = useT();
  const trace = error instanceof ApiError ? error.traceId : null;
  return (
    <div className="flex h-screen items-center justify-center bg-app p-4">
      <div role="alert" className="nx-card flex max-w-md flex-col gap-3 p-6">
        <p className="text-sm font-semibold text-fg">{t("app.contextError")}</p>
        {trace ? <p className="font-mono text-xs text-muted">{t("app.error.trace", { trace })}</p> : null}
        <Button variant="primary" onClick={retry}>
          {t("app.retry")}
        </Button>
      </div>
    </div>
  );
}
