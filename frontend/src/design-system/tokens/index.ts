// Typed tokens module (owner P08): the frozen docs/spec/ui/tokens.json, imported at build time.
import raw from "@spec/ui/tokens.json";
import type { UiTokens } from "./css";

export const tokens = raw as unknown as UiTokens;
export * from "./css";
