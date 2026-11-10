// Sign-in routes (owner P08, FIX-UI-LOGIN-W03 UL-5; CCR P08-6; DEV-ADDENDUM D-P08: DEV banner on every page).
// * "/"              signed out → entry screen with "Iniciar sesión"; signed in (or silently restored) → /m/inicio.
// * "/m/*"           RequireAuth: restore silently from the stored refresh token, else redirect straight to the
//                    authorize endpoint with the current path as return path (Keycloak's SSO cookie answers without a form).
// * "/oidc/callback" "Conectando…" → code exchange → history.replace(return path); a state/nonce mismatch or IdP error
//                    shows an error card with "Volver a intentar" (never loops).
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { useT } from "@i18n/index";
import { Button } from "@ds/components/Button";
import { TokenScope } from "@ds/theme/ThemeProvider";
import { Brand } from "@shell/Brand";
import { DevBanner } from "@shell/DevBanner";
import { AuthError, DEFAULT_RETURN_PATH, type AuthSession } from "@lib/auth/oidc";
import { BootLoading } from "./Boot";

/** Placeholder brand (tokens.json brand.placeholder_name) shown before /ui/context is reachable. */
const BRAND = "NexusERP";

function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <TokenScope variant="internal" className="flex min-h-screen flex-col bg-app font-sans text-body">
      <DevBanner />
      <main id="nx-main" data-nx-region="main" className="flex flex-1 items-center justify-center p-4">
        {children}
      </main>
    </TokenScope>
  );
}

function Status({ text }: { text: string }) {
  return (
    <AuthFrame>
      <p role="status" aria-live="polite" className="text-sm text-muted">
        {text}
      </p>
    </AuthFrame>
  );
}

export function EntryScreen({ onSignIn, busy = false }: { onSignIn: () => void; busy?: boolean }) {
  const t = useT();
  return (
    <AuthFrame>
      <section aria-labelledby="nx-entry-title" className="nx-card flex w-full max-w-sm flex-col items-center gap-5 p-8 text-center">
        <Brand name={BRAND} to="/" />
        <div className="flex flex-col gap-2">
          <h1 id="nx-entry-title" className="text-xl font-bold text-fg">
            {t("auth.entry.title")}
          </h1>
          <p className="text-sm text-muted">{t("auth.entry.body")}</p>
        </div>
        <Button variant="primary" className="w-full" onClick={onSignIn} loading={busy} disabled={busy}>
          {t("auth.signIn")}
        </Button>
      </section>
    </AuthFrame>
  );
}

export function AuthErrorCard({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const t = useT();
  const code = error instanceof AuthError ? error.code : "unknown";
  return (
    <AuthFrame>
      <div role="alert" className="nx-card flex w-full max-w-md flex-col gap-3 p-6">
        <h1 className="text-base font-semibold text-fg">{t("auth.error.title")}</h1>
        <p className="text-sm text-body">{t("auth.error.body")}</p>
        <p className="font-mono text-xs text-muted">{t("auth.error.code", { code })}</p>
        <Button variant="primary" onClick={onRetry}>
          {t("auth.retry")}
        </Button>
      </div>
    </AuthFrame>
  );
}

function useSignIn(auth: AuthSession) {
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const start = useCallback(
    (path: string) => {
      setError(null);
      setBusy(true);
      auth.signIn(path).catch((e: unknown) => {
        setBusy(false);
        setError(e);
      });
    },
    [auth],
  );
  return { error, busy, start };
}

export function EntryRoute({ auth }: { auth: AuthSession }) {
  const t = useT();
  const [state, setState] = useState<"in" | "checking" | "out">(() => (auth.isSignedIn() ? "in" : auth.hasStoredSession() ? "checking" : "out"));
  const signIn = useSignIn(auth);
  useEffect(() => {
    if (state !== "checking") return;
    let alive = true;
    auth
      .restore()
      .then((ok) => alive && setState(ok ? "in" : "out"))
      .catch(() => alive && setState("out"));
    return () => {
      alive = false;
    };
  }, [auth, state]);
  if (state === "in") return <Navigate to={DEFAULT_RETURN_PATH} replace />;
  if (state === "checking") return <Status text={t("app.loading")} />;
  if (signIn.error) return <AuthErrorCard error={signIn.error} onRetry={() => signIn.start(DEFAULT_RETURN_PATH)} />;
  return <EntryScreen busy={signIn.busy} onSignIn={() => signIn.start(DEFAULT_RETURN_PATH)} />;
}

export function RequireAuth({ auth, children }: { auth: AuthSession; children: ReactNode }) {
  const t = useT();
  const loc = useLocation();
  const [state, setState] = useState<"in" | "checking" | "redirect">(() => (auth.isSignedIn() ? "in" : "checking"));
  const signIn = useSignIn(auth);
  const returnPath = loc.pathname + loc.search + loc.hash;
  useEffect(() => {
    if (state !== "checking") return;
    let alive = true;
    const start = signIn.start;
    const redirect = () => {
      if (!alive) return;
      setState("redirect");
      start(returnPath);
    };
    auth
      .restore()
      .then((ok) => (ok ? alive && setState("in") : redirect()))
      .catch(redirect);
    return () => {
      alive = false;
    };
  }, [auth, state, returnPath, signIn.start]);
  if (state === "in") return <>{children}</>;
  if (signIn.error) return <AuthErrorCard error={signIn.error} onRetry={() => signIn.start(returnPath)} />;
  if (state === "redirect") return <Status text={t("auth.redirecting")} />;
  return <BootLoading />;
}

export function CallbackRoute({ auth }: { auth: AuthSession }) {
  const t = useT();
  const nav = useNavigate();
  const loc = useLocation();
  const started = useRef(false);
  const [error, setError] = useState<unknown>(null);
  const signIn = useSignIn(auth);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    auth
      .handleCallback(loc.search)
      .then((path) => nav(path, { replace: true }))
      .catch((e: unknown) => setError(e));
  }, [auth, loc.search, nav]);
  const failure = error ?? signIn.error;
  if (failure) return <AuthErrorCard error={failure} onRetry={() => signIn.start(DEFAULT_RETURN_PATH)} />;
  return <Status text={signIn.busy ? t("auth.redirecting") : t("auth.connecting")} />;
}
