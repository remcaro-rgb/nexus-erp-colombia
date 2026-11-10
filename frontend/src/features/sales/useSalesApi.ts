// Sales API bound to the shell's client and active company (falls back to a plain client outside the shell).
import { useMemo } from "react";
import { createApiClient } from "@lib/api/client";
import { can } from "@lib/permissions";
import { useOptionalShell } from "@shell/ShellContext";
import { SalesApi } from "./api";

export function useSalesApi(): { api: SalesApi; companyId: string | null; can: (code: string) => boolean } {
  const shell = useOptionalShell();
  const companyId = shell?.companyId ?? null;
  const client = shell?.api;
  const api = useMemo(() => new SalesApi(client ?? createApiClient(), companyId), [client, companyId]);
  return { api, companyId, can: (code: string) => (shell ? can(shell.perms, code) : false) };
}
