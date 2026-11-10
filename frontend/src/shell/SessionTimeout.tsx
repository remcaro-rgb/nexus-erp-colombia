// Session timeout warning (owner P08): after `idleMinutes` without activity the session ends (OIDC logout);
// 60 s before, a dialog warns with a live countdown and "Seguir trabajando".
import { useEffect, useRef, useState } from "react";
import { useT } from "@i18n/index";
import { Dialog } from "@ds/components/Dialog";
import { Button } from "@ds/components/Button";

export function SessionTimeout({ idleMinutes, onExpire, onContinue, warnSeconds = 60 }: { idleMinutes: number; onExpire: () => void; onContinue?: () => void; warnSeconds?: number }) {
  const t = useT();
  const last = useRef(Date.now());
  const [left, setLeft] = useState<number | null>(null);
  useEffect(() => {
    const bump = () => {
      if (left === null) last.current = Date.now();
    };
    const evs = ["pointerdown", "keydown", "wheel", "touchstart"] as const;
    for (const e of evs) window.addEventListener(e, bump, { passive: true });
    const h = window.setInterval(() => {
      const remaining = Math.round((last.current + idleMinutes * 60_000 - Date.now()) / 1000);
      if (remaining <= 0) {
        window.clearInterval(h);
        onExpire();
      } else if (remaining <= warnSeconds) setLeft(remaining);
      else setLeft(null);
    }, 1000);
    return () => {
      window.clearInterval(h);
      for (const e of evs) window.removeEventListener(e, bump);
    };
  }, [idleMinutes, onExpire, warnSeconds, left]);
  return (
    <Dialog
      open={left !== null}
      onOpenChange={() => undefined}
      title={t("session.warning.title")}
      footer={
        <Button
          variant="primary"
          onClick={() => {
            last.current = Date.now();
            setLeft(null);
            onContinue?.();
          }}
        >
          {t("session.warning.continue")}
        </Button>
      }
    >
      <p role="timer" aria-live="polite" className="text-sm text-body">
        {t("session.warning.body", { seconds: left ?? 0 })}
      </p>
    </Dialog>
  );
}
