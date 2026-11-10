// Permission helpers (owner P08). The UI is NOT a security boundary (UI-CONTRACT §11): these checks only hide
// convenience controls. Deny-by-default: an unknown or empty permission set grants nothing.
export type PermissionSet = ReadonlySet<string>;

export function toPermissionSet(codes: readonly string[] | null | undefined): PermissionSet {
  return new Set((codes ?? []).filter((c) => typeof c === "string" && c.length > 0));
}

export function can(perms: PermissionSet, code: string | null | undefined): boolean {
  if (!code) return false;
  return perms.has(code);
}

/** Stable hash of a permission set (part of every client cache key). */
export function permissionHash(perms: PermissionSet): string {
  const s = [...perms].sort().join(",");
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

/** Field masks from /iam/me (`field_masks`): { "<resource>": ["field", ...] } = fields the user may NOT see. */
export type FieldMasks = Readonly<Record<string, readonly string[]>>;

export function isFieldVisible(masks: FieldMasks, resource: string, field: string): boolean {
  const hidden = masks[resource];
  return !(hidden && hidden.includes(field));
}
