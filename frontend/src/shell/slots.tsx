// Slot registry (owner P08): modules mount content into shell regions without editing the shell.
// * "ai-panel": the conversation UI (P19, features/ai_agent) — the shell owns only the frame.
// * "side-panel:<id>": document side panels. * "home-widget:<id>": role home widgets.
// The AI bridge passes page context = current route + record ids from the URL only (never field values).
import { useSyncExternalStore, type ComponentType } from "react";

export interface AiPageContext {
  route: string;
  moduleId: string | null;
  recordIds: string[];
}

export interface AiPanelSlotProps {
  pageContext: AiPageContext;
  pendingPrompt: string | null;
  consumePrompt: () => void;
}

type SlotMap = Map<string, { component: ComponentType<any>; permission?: string }>; // eslint-disable-line @typescript-eslint/no-explicit-any

const slots: SlotMap = new Map();
const listeners = new Set<() => void>();
let version = 0;
let pendingPrompt: string | null = null;

function emit() {
  version++;
  for (const l of listeners) l();
}

export function registerSlot<P>(id: string, component: ComponentType<P>, permission?: string): () => void {
  slots.set(id, { component, permission });
  emit();
  return () => {
    if (slots.get(id)?.component === component) {
      slots.delete(id);
      emit();
    }
  };
}

export function getSlot(id: string) {
  return slots.get(id) ?? null;
}

export function listSlots(prefix: string) {
  return [...slots.entries()].filter(([k]) => k.startsWith(prefix)).map(([id, v]) => ({ id, ...v }));
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function useSlotsVersion(): number {
  return useSyncExternalStore(subscribe, () => version);
}

/** Command palette "Ask AI": queue a prompt for the AI panel content (P19) to pick up. */
export function queueAiPrompt(q: string) {
  pendingPrompt = q;
  emit();
}

export function takeAiPrompt(): string | null {
  const p = pendingPrompt;
  pendingPrompt = null;
  if (p !== null) emit();
  return p;
}

export function peekAiPrompt(): string | null {
  return pendingPrompt;
}

/** Record ids visible in the URL (uuid-like or numeric path segments) — the only record data the AI may receive. */
export function pageContextFrom(pathname: string, moduleId: string | null): AiPageContext {
  const ids = pathname.split("/").filter((s) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s) || /^\d+$/.test(s));
  return { route: pathname, moduleId, recordIds: ids };
}
