// Identifiers (owner P08): Idempotency-Key for every state-changing API action (constitution).
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
