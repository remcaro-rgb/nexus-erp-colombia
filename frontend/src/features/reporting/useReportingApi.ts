// Reporting API bound to the shell's client and active company (falls back to a plain client outside the shell).
import { useMemo } from "react";
import { createApiClient } from "@lib/api/client";
import { can } from "@lib/permissions";
import { useOptionalShell } from "@shell/ShellContext";
import { ReportingApi } from "./api";

export function useReportingApi(): { api: ReportingApi; companyId: string | null; can: (code: string) => boolean } {
  const shell = useOptionalShell();
  const companyId = shell?.companyId ?? null;
  const client = shell?.api;
  const api = useMemo(() => new ReportingApi(client ?? createApiClient(), companyId), [client, companyId]);
  return { api, companyId, can: (code: string) => (shell ? can(shell.perms, code) : false) };
}
