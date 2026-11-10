// Export registration (owner P08): the current view declares what it can export; the module bar's export button
// calls P18 (POST /api/v1/dataio/exports, policy-applied) and is disabled with a tooltip otherwise.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export interface ExportSpec {
  entity: string;
  params?: Record<string, string>;
}

const Ctx = createContext<{ spec: ExportSpec | null; set: (s: ExportSpec | null) => void }>({ spec: null, set: () => undefined });

export function ExportProvider({ children }: { children: ReactNode }) {
  const [spec, set] = useState<ExportSpec | null>(null);
  return <Ctx.Provider value={{ spec, set }}>{children}</Ctx.Provider>;
}

export function useExportSpec() {
  return useContext(Ctx).spec;
}

/** Called by a page that supports export. */
export function useRegisterExport(spec: ExportSpec | null) {
  const { set } = useContext(Ctx);
  const key = JSON.stringify(spec);
  useEffect(() => {
    set(spec);
    return () => set(null);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
}
