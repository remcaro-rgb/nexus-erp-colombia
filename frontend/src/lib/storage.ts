// Per-user UI preferences in localStorage (owner P08): sidebar collapsed, AI panel open, theme, density, saved
// grid views, dashboard layout. Never stores business data, tokens or secrets.
export function prefKey(userId: string | null | undefined, name: string): string {
  return `nexus:${userId || "anon"}:${name}`;
}

export function readPref<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

export function writePref(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable (private mode): preference is not persisted */
  }
}
