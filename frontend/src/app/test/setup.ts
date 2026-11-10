// Vitest setup (owner P08).
import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { setAuth } from "@lib/auth/oidc";
import { fakeAuth } from "./auth";

// Every test starts with a synthetic signed-in session (FIX-UI-LOGIN-W03); sign-in tests pass their own.
beforeEach(() => setAuth(fakeAuth()));

afterEach(() => {
  cleanup();
  setAuth(null);
  try {
    window.sessionStorage.clear();
  } catch {
    /* storage unavailable */
  }
  try {
    window.localStorage.clear();
  } catch {
    /* storage unavailable */
  }
  document.documentElement.className = "";
  document.documentElement.removeAttribute("style");
});

// jsdom lacks these browser APIs used by the shell and Radix.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}
class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver ??= RO;
Element.prototype.scrollIntoView ??= function scrollIntoView() {};

// Canvas is not implemented in jsdom: charts fall back to their accessible table/summary.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement["getContext"];
