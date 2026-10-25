/**
 * Why a chat turn failed, and the words the user reads for it.
 *
 * ONE set of codes, ONE table from code to wording, ONE look on the bubble
 * (components/ChatMessage.tsx). Nothing else in the app decides what a failure
 * says. The contract with the backend is docs/architecture/sse-contract.md: the
 * server sends a code in the SSE `error` event and never any text.
 *
 * Pure on purpose (vitest `node`, no DOM) — same pattern as lib/turnLifecycle.ts.
 * It does not import lib/api.ts: the error classes that file throws are
 * recognised by `name` / `status`, so this stays free of axios and Amplify.
 *
 * Wording comes from two places:
 *   persona  the character's own `ui_strings` line — for failures the user
 *            cannot fix. It is the character speaking, in the reply bubble.
 *   neutral  a fixed i18n string — for failures the user must act on.
 */

import type { UiStrings } from './characterCopy'

/** Codes the backend emits (docs/architecture/sse-contract.md). */
export const BACKEND_CODES = [
  'provider_billing',
  'provider_auth',
  'provider_unavailable',
  'internal',
  'no_answer',
] as const

/** Codes only this client produces. */
export const FRONTEND_CODES = [
  'interrupted',
  'network',
  'timeout',
  'session_expired',
  'rate_limited',
  'bad_request',
  'server',
] as const

export type KnownErrorCode = (typeof BACKEND_CODES)[number] | (typeof FRONTEND_CODES)[number]

/** What the user can be asked to do besides retrying. */
export type ChatErrorAction = 'sign_in' | 'llm_settings' | null

export interface ChatError {
  /** A KnownErrorCode, or any string a newer backend sends (worded as `error_system`). */
  code: string
  retryable: boolean
  action: ChatErrorAction
}

/** Where a code's wording lives. */
export type CopySource =
  | { kind: 'persona'; key: 'error_system' | 'error_stream' | 'error_unavailable' }
  | { kind: 'i18n'; key: string }

const COPY: Record<KnownErrorCode, CopySource> = {
  provider_billing: { kind: 'persona', key: 'error_system' },
  provider_auth: { kind: 'persona', key: 'error_system' },
  provider_unavailable: { kind: 'persona', key: 'error_system' },
  internal: { kind: 'persona', key: 'error_system' },
  no_answer: { kind: 'persona', key: 'error_unavailable' },
  interrupted: { kind: 'persona', key: 'error_stream' },
  network: { kind: 'persona', key: 'error_stream' },
  timeout: { kind: 'persona', key: 'error_stream' },
  session_expired: { kind: 'i18n', key: 'chat.error_session_expired' },
  rate_limited: { kind: 'i18n', key: 'chat.error_rate_limited' },
  bad_request: { kind: 'i18n', key: 'chat.error_bad_request' },
  server: { kind: 'persona', key: 'error_system' },
}

/** The wording source for a code. An unknown code is worded like `provider_unavailable`. */
export function errorCopySource(code: string): CopySource {
  return (COPY as Record<string, CopySource | undefined>)[code] ?? COPY.provider_unavailable
}

/** The sentence to show for `error`. `t` is i18next's `t`, or any key -> string. */
export function errorCopy(
  error: Pick<ChatError, 'code'>,
  ui: Pick<UiStrings, 'error_system' | 'error_stream' | 'error_unavailable'>,
  t: (key: string) => string,
): string {
  const source = errorCopySource(error.code)
  return source.kind === 'persona' ? ui[source.key] : t(source.key)
}

/** Codes that can be sent again as they are. The rest need the user to change something. */
const NOT_RETRYABLE = new Set<string>(['session_expired', 'bad_request'])

function make(code: string, retryable = !NOT_RETRYABLE.has(code), action: ChatErrorAction = null): ChatError {
  return { code, retryable, action }
}

/** From the SSE `error` event. Malformed payloads count as `internal`. */
export function errorFromEvent(payload: unknown): ChatError {
  const p = (payload && typeof payload === 'object' ? payload : {}) as { code?: unknown; retryable?: unknown }
  const code = typeof p.code === 'string' && p.code ? p.code : 'internal'
  // The backend sends retryable:true for every code it emits; only an explicit
  // false turns the button off.
  return make(code, p.retryable !== false)
}

/** From a non-2xx answer to POST /chat, before any stream. */
export function errorFromHttp(status: number): ChatError {
  if (status === 401) return make('session_expired', false, 'sign_in')
  if (status === 429) return make('rate_limited')
  if (status >= 400 && status < 500 && status !== 403 && status !== 408) return make('bad_request')
  return make('server')
}

/**
 * From whatever streamChat threw. `null` means the user cancelled (stop button,
 * new chat, switching conversation): that is not a failure and shows nothing.
 *
 * Recognised by name, not instanceof — see the header.
 */
export function errorFromThrow(err: unknown): ChatError | null {
  const e = err as { name?: string; status?: unknown } | null | undefined
  const name = e?.name
  if (name === 'AbortError') return null
  if (name === 'StreamTimeoutError') return make('timeout')
  if (name === 'HttpStatusError' && typeof e?.status === 'number') return errorFromHttp(e.status)
  if (name === 'StreamInterruptedError') return make('interrupted')
  // fetch() rejects with a TypeError when it never reached the server.
  if (err instanceof TypeError) return make('network')
  if (typeof navigator !== 'undefined' && navigator.onLine === false) return make('network')
  return make('interrupted')
}

/**
 * The link under the error, if any. A BYOK key that failed with ECA failing
 * too (`provider_notice.answered_by` null) points at the key settings; a
 * code's own action (sign in) wins.
 */
export function errorAction(error: ChatError, providerNoticeFailed: boolean): ChatErrorAction {
  if (error.action) return error.action
  return providerNoticeFailed ? 'llm_settings' : null
}

/** Whether the retry button shows: a retryable error on the newest assistant turn, not mid-stream. */
export function showRetry(error: ChatError, isLatestTurn: boolean, isGenerating: boolean): boolean {
  return error.retryable && isLatestTurn && !isGenerating
}

/** Codes where the server may still have saved the turn, so recovery polling is worth it. */
export function shouldRecover(error: ChatError): boolean {
  return error.code === 'interrupted' || error.code === 'network'
}

/** What a stored history row says about how its turn ended. */
export function errorFromHistory(row: {
  error_code?: string | null
  interrupted?: boolean | null
}): { error?: ChatError; interrupted: boolean } {
  if (row.error_code) return { error: make(row.error_code), interrupted: false }
  return { interrupted: row.interrupted === true }
}
