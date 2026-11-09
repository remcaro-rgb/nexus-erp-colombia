// Breakpoints from tokens.json (owner P08): xl ≥ 1280 (docked AI panel), lg 1024–1279, md 768–1023, sm < 768.
import { useEffect, useState } from "react";
import { tokens } from "@ds/tokens";

export type Breakpoint = "sm" | "md" | "lg" | "xl";

export function breakpointFor(width: number): Breakpoint {
  const b = tokens.breakpoints;
  if (width >= (b.xl ?? 1280)) return "xl";
  if (width >= (b.lg ?? 1024)) return "lg";
  if (width >= (b.md ?? 768)) return "md";
  return "sm";
}

export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>(() => breakpointFor(window.innerWidth));
  useEffect(() => {
    const on = () => setBp(breakpointFor(window.innerWidth));
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return bp;
}
