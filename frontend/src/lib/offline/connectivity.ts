// Connectivity state (owner P08): online/offline from the browser plus an optional server probe (/api/health/live).
// There is NO write queue for posting commands (C1-12): offline mode only reads cached catalogs and keeps unposted drafts.
import { useEffect, useState } from "react";

export type Connectivity = "online" | "offline";

export function currentConnectivity(): Connectivity {
  return typeof navigator !== "undefined" && navigator.onLine === false ? "offline" : "online";
}

export function useConnectivity(): Connectivity {
  const [state, setState] = useState<Connectivity>(currentConnectivity);
  useEffect(() => {
    const on = () => setState("online");
    const off = () => setState("offline");
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);
  return state;
}
