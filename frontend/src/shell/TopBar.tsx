// Top bar frame (owner P08): 64 px, surface, border-b, shadow-sm, px-24. Shared by the internal shell and the
// portal shell variant so both use identical geometry and tokens.
import type { ReactNode } from "react";

export function TopBar({ left, right }: { left: ReactNode; right: ReactNode }) {
  return (
    <header
      data-nx-region="header"
      className="relative z-[30] flex h-16 shrink-0 items-center gap-3 border-b border-line bg-surface px-4 shadow-sm md:px-6"
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">{left}</div>
      <div className="flex shrink-0 items-center gap-1">{right}</div>
    </header>
  );
}
