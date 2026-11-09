// Error boundary (owner P08): a failing region shows a message + "Reintentar" without breaking the shell.
import { Component, type ErrorInfo, type ReactNode } from "react";
import { useT } from "@i18n/index";
import { ApiError } from "@lib/api/client";

function Fallback({ error, reset }: { error: unknown; reset: () => void }) {
  const t = useT();
  const trace = error instanceof ApiError ? error.traceId : null;
  return (
    <div role="alert" className="nx-card m-4 flex flex-col items-start gap-2 p-6">
      <h2 className="text-base font-bold text-fg">{t("app.error.title")}</h2>
      <p className="text-sm text-body">{t("app.error.body")}</p>
      {trace ? <p className="font-mono text-xs text-muted">{t("app.error.trace", { trace })}</p> : null}
      <button type="button" onClick={reset} className="min-h-8 rounded-control bg-brand px-3 text-sm font-semibold text-white dark:text-slate-950">
        {t("app.retry")}
      </button>
    </div>
  );
}

export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: unknown }> {
  override state: { error: unknown } = { error: null };

  static getDerivedStateFromError(error: unknown) {
    return { error };
  }

  override componentDidCatch(_error: unknown, _info: ErrorInfo) {
    // Reported through the API's own telemetry; nothing is logged with user data here.
  }

  override componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  override render() {
    if (this.state.error) return <Fallback error={this.state.error} reset={() => this.setState({ error: null })} />;
    return this.props.children;
  }
}
