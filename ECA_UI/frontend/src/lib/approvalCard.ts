/**
 * The approval card's data and its states (stage 4, plan section 7).
 *
 * The server's `approval_required` payload is trusted for shape only: it is
 * re-read field by field here, and every value becomes a plain string. The card
 * prints those strings as text and never as markdown or HTML.
 *
 * Pure (vitest `node`, no DOM), like lib/turnLifecycle.ts.
 *
 * States:
 *   pending      waiting for the user
 *   approving    a decision is on its way (the resumed turn is streaming)
 *   done         approved; the outcome line is in the reply text
 *   declined     the user declined
 *   expired      past `expires_at`
 *   invalidated  no longer answerable (a new message, a changed plugin, a block)
 *   error        the answer did not reach the server; both buttons stay
 */

import type { ApprovalField } from './approvalDisplay'

export type ApprovalDecision = 'approve' | 'decline'

export type ApprovalState = 'pending' | 'approving' | 'done' | 'declined' | 'expired' | 'invalidated' | 'error'

export interface ApprovalPayload {
  thread_id: string
  action_id: string
  tool: string
  plugin_id: string
  display: ApprovalField[]
  recipients: string[]
  provenance: Array<{ tool: string; source_label: string }>
  /** ISO timestamp. */
  expires_at: string
}

export interface ApprovalCardData {
  payload: ApprovalPayload
  state: ApprovalState
}

/** What GET /me/actions/pending returns per action: the payload plus the status. */
export interface PendingAction extends ApprovalPayload {
  status: string
}

const str = (v: unknown): string => (typeof v === 'string' ? v : v == null ? '' : String(v))

/** The payload from an SSE event or the pending route; null when it cannot be answered (no ids). */
export function parsePayload(data: unknown): ApprovalPayload | null {
  const d = data as Record<string, unknown> | null | undefined
  if (!d || typeof d !== 'object') return null
  const thread_id = str(d.thread_id)
  const action_id = str(d.action_id)
  if (!thread_id || !action_id) return null
  const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])
  return {
    thread_id,
    action_id,
    tool: str(d.tool),
    plugin_id: str(d.plugin_id),
    display: list(d.display).map((f) => {
      const row = (f ?? {}) as Record<string, unknown>
      return { label: str(row.label), value: str(row.value) }
    }),
    recipients: list(d.recipients).map(str),
    provenance: list(d.provenance).map((p) => {
      const row = (p ?? {}) as Record<string, unknown>
      return { tool: str(row.tool), source_label: str(row.source_label) }
    }),
    expires_at: str(d.expires_at),
  }
}

/** The distinct source labels the proposal rests on, in order. */
export function provenanceLabels(payload: ApprovalPayload): string[] {
  const out: string[] = []
  for (const p of payload.provenance) {
    const label = p.source_label || p.tool
    if (label && !out.includes(label)) out.push(label)
  }
  return out
}

/** Whether the card's time is up. An unreadable timestamp counts as not expired: the server decides. */
export function isExpired(payload: ApprovalPayload, nowMs: number): boolean {
  const at = Date.parse(payload.expires_at)
  return Number.isFinite(at) && nowMs >= at
}

/** Milliseconds until expiry, or null when there is no readable deadline. */
export function msUntilExpiry(payload: ApprovalPayload, nowMs: number): number | null {
  const at = Date.parse(payload.expires_at)
  return Number.isFinite(at) ? Math.max(0, at - nowMs) : null
}

/** Whether the buttons are offered in this state. */
export function canAnswer(state: ApprovalState): boolean {
  return state === 'pending' || state === 'error'
}

/** Reply text may be read as markdown only once the card is settled: until then it is the payload's summary line. */
export function replyIsPlainText(state: ApprovalState): boolean {
  return state !== 'done' && state !== 'declined'
}

/**
 * The card's state after the resumed turn ended, from its `turn_status` actions.
 * An action the turn does not list falls back to what the user chose.
 */
export function stateAfterTurn(
  actionId: string,
  decision: ApprovalDecision,
  actions: Array<{ action_id?: unknown; status?: unknown }> | null,
): ApprovalState {
  const mine = actions?.find((a) => a.action_id === actionId)
  switch (mine?.status) {
    case 'declined':
      return 'declined'
    case 'expired':
      return 'expired'
    case 'invalidated':
      return 'invalidated'
    case 'succeeded':
    case 'failed':
    case 'unknown':
      return 'done'
    default:
      return decision === 'approve' ? 'done' : 'declined'
  }
}

/**
 * The card's state when the server refused the answer (409/404) or the stream
 * was cut: read it from the pending route. Still listed → answerable again;
 * listed as expired → expired; absent → no longer answerable.
 */
export function stateFromPending(actionId: string, pending: PendingAction[] | null): ApprovalState {
  const mine = pending?.find((a) => a.action_id === actionId)
  if (!mine) return 'invalidated'
  return mine.status === 'expired' ? 'expired' : 'pending'
}
