/**
 * What a failed "load this conversation" request means.
 *
 *   cancelled  the user moved on (another click, a new chat): say nothing.
 *   missing    404 — the conversation is gone. On restore the stale pointer is
 *              dropped and a fresh greeting stays; it is not an error.
 *   failed     anything else (offline, 5xx, 401…). The conversation exists but
 *              could not be read, so the panel must say so and offer a retry —
 *              never an empty panel, and never a greeting that makes an old
 *              conversation look new.
 *
 * Pure (vitest `node`); the caller decides what to do for each outcome.
 */
export type SessionLoadOutcome = 'cancelled' | 'missing' | 'failed'

export function sessionLoadOutcome(err: unknown): SessionLoadOutcome {
  const e = err as { name?: string; code?: string; response?: { status?: number } } | null | undefined
  if (e?.name === 'AbortError' || e?.code === 'ERR_CANCELED') return 'cancelled'
  if (e?.response?.status === 404) return 'missing'
  return 'failed'
}
