// Offline indicator (owner P08): announced banner while the browser is offline. Nothing posts offline (C1-12).
import { WifiOff } from "lucide-react";
import { useT } from "@i18n/index";
import { useConnectivity } from "@lib/offline/connectivity";

export function OfflineIndicator() {
  const t = useT();
  const state = useConnectivity();
  if (state === "online") return null;
  return (
    <div role="status" data-nx-region="offline" className="flex shrink-0 items-center justify-center gap-2 bg-slate-900 px-4 py-1.5 text-xs font-semibold text-white">
      <WifiOff aria-hidden="true" size={14} />
      {t("offline.banner")}
    </div>
  );
}
