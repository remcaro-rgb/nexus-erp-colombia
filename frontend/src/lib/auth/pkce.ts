// PKCE (RFC 7636, S256) and small JOSE helpers for the SPA sign-in (owner P08, FIX-UI-LOGIN-W03 UL-5).
// WebCrypto only (crypto.getRandomValues + crypto.subtle.digest); no dependency.

export function base64UrlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function base64UrlDecode(text: string): string {
  const b64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/** Cryptographically random URL-safe string (`bytes` of entropy). 32 bytes → a 43-character PKCE verifier. */
export function randomString(bytes = 32): string {
  const buf = new Uint8Array(bytes);
  globalThis.crypto.getRandomValues(buf);
  return base64UrlEncode(buf);
}

/** `code_challenge` for `code_challenge_method=S256`: BASE64URL(SHA-256(ASCII(code_verifier))). */
export async function pkceChallenge(codeVerifier: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(codeVerifier));
  return base64UrlEncode(new Uint8Array(digest));
}

/** Payload of a compact JWT (no signature check: the id_token comes straight from the token endpoint over TLS). */
export function decodeJwtPayload(jwt: string): Record<string, unknown> | null {
  const parts = jwt.split(".");
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const value: unknown = JSON.parse(base64UrlDecode(parts[1]));
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
