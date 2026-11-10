// TEST-ONLY synthetic sign-in session (owner P08, FIX-UI-LOGIN-W03). Never imported by application code: the
// production bundle builds its session from the real OIDC flow (lib/auth/oidc.ts). Synthetic token, no real identity.
import type { AuthSession } from "@lib/auth/oidc";

export interface FakeAuth extends AuthSession {
  signedIn: boolean;
  signIns: string[];
  logouts: number;
  refreshes: number;
  token: string;
}

export function fakeAuth(opts: { signedIn?: boolean; stored?: boolean; refreshOk?: boolean; callbackPath?: string; token?: string } = {}): FakeAuth {
  const a: FakeAuth = {
    signedIn: opts.signedIn ?? true,
    signIns: [],
    logouts: 0,
    refreshes: 0,
    token: opts.token ?? "synthetic-access-token",
    accessToken: () => (a.signedIn ? a.token : null),
    isSignedIn: () => a.signedIn,
    hasStoredSession: () => Boolean(opts.stored),
    restore: async () => {
      if (!a.signedIn && opts.stored && opts.refreshOk) a.signedIn = true;
      return a.signedIn;
    },
    refresh: async () => {
      a.refreshes += 1;
      return Boolean(opts.refreshOk);
    },
    signIn: async (returnPath?: string) => {
      a.signIns.push(returnPath ?? "");
    },
    handleCallback: async () => {
      a.signedIn = true;
      return opts.callbackPath ?? "/m/inicio";
    },
    logout: async () => {
      a.logouts += 1;
      a.signedIn = false;
    },
  };
  return a;
}
