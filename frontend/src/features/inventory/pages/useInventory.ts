// Shared data hook for the inventory pages: an InventoryApi bound to the shell's current company.
import { useEffect, useMemo, useState } from "react";
import { useShell } from "@shell/ShellContext";
import { ApiError } from "@lib/api/client";
import { InventoryApi } from "../api";

export function useInventoryApi(): InventoryApi {
  const shell = useShell();
  return useMemo(() => new InventoryApi(shell.api, shell.companyId), [shell.api, shell.companyId]);
}

export function useLoad<T>(load: (signal: AbortSignal) => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const ctrl = new AbortController();
    setError(null);
    load(ctrl.signal)
      .then((d) => setData(d))
      .catch((e: unknown) => {
        if (!ctrl.signal.aborted) setError(e instanceof ApiError ? `${e.code}: ${e.message}` : String(e));
      });
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}

export function errorText(e: unknown): string {
  return e instanceof ApiError ? `${e.code}: ${e.message}` : String(e);
}
