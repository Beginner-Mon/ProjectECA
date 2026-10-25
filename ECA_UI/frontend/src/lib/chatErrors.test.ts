import { describe, expect, it } from 'vitest'
import {
  BACKEND_CODES,
  FRONTEND_CODES,
  errorAction,
  errorCopy,
  errorCopySource,
  errorFromEvent,
  errorFromHistory,
  errorFromHttp,
  errorFromThrow,
  shouldRecover,
  showRetry,
  type KnownErrorCode,
} from './chatErrors'
import { FALLBACK_UI_STRINGS } from './characterCopy'

/** The plan's code table (docs/architecture/sse-contract.md), one row per code. */
const TABLE: Record<KnownErrorCode, string> = {
  provider_billing: 'persona:error_system',
  provider_auth: 'persona:error_system',
  provider_unavailable: 'persona:error_system',
  internal: 'persona:error_system',
  no_answer: 'persona:error_unavailable',
  interrupted: 'persona:error_stream',
  network: 'persona:error_stream',
  timeout: 'persona:error_stream',
  session_expired: 'i18n:chat.error_session_expired',
  rate_limited: 'i18n:chat.error_rate_limited',
  bad_request: 'i18n:chat.error_bad_request',
  server: 'persona:error_system',
}

const sourceOf = (code: string) => {
  const s = errorCopySource(code)
  return `${s.kind}:${s.key}`
}

describe('code -> wording table', () => {
  it('covers every backend and frontend code, and nothing else', () => {
    expect([...BACKEND_CODES, ...FRONTEND_CODES].sort()).toEqual(Object.keys(TABLE).sort())
  })

  it.each(Object.entries(TABLE))('%s -> %s', (code, expected) => {
    expect(sourceOf(code)).toBe(expected)
  })

  it('words an unknown backend code like provider_unavailable (error_system)', () => {
    expect(sourceOf('some_future_code')).toBe('persona:error_system')
  })

  it('errorCopy picks the persona line or the neutral i18n string', () => {
    const ui = { ...FALLBACK_UI_STRINGS.vi, error_system: 'SYS', error_stream: 'STREAM', error_unavailable: 'UNAVAIL' }
    const t = (k: string) => `t(${k})`
    expect(errorCopy({ code: 'provider_billing' }, ui, t)).toBe('SYS')
    expect(errorCopy({ code: 'interrupted' }, ui, t)).toBe('STREAM')
    expect(errorCopy({ code: 'no_answer' }, ui, t)).toBe('UNAVAIL')
    expect(errorCopy({ code: 'session_expired' }, ui, t)).toBe('t(chat.error_session_expired)')
  })
})

describe('errorFromEvent', () => {
  it('reads code and retryable from the payload; no action', () => {
    expect(errorFromEvent({ code: 'provider_billing', retryable: true, request_id: 'r' })).toEqual({
      code: 'provider_billing', retryable: true, action: null,
    })
  })

  it('only an explicit retryable:false turns retry off', () => {
    expect(errorFromEvent({ code: 'internal' }).retryable).toBe(true)
    expect(errorFromEvent({ code: 'internal', retryable: false }).retryable).toBe(false)
  })

  it('keeps an unknown code as sent', () => {
    expect(errorFromEvent({ code: 'new_thing' }).code).toBe('new_thing')
  })

  it.each([null, undefined, 'x', {}, { code: '' }, { code: 5 }])('malformed payload %j is internal', (p) => {
    expect(errorFromEvent(p).code).toBe('internal')
  })
})

describe('errorFromHttp', () => {
  it('401 -> session_expired, sign-in action, no retry', () => {
    expect(errorFromHttp(401)).toEqual({ code: 'session_expired', retryable: false, action: 'sign_in' })
  })
  it('429 -> rate_limited, retryable', () => {
    expect(errorFromHttp(429)).toEqual({ code: 'rate_limited', retryable: true, action: null })
  })
  it.each([400, 413, 422])('%i -> bad_request, not retryable', (s) => {
    expect(errorFromHttp(s)).toEqual({ code: 'bad_request', retryable: false, action: null })
  })
  it.each([500, 502, 503, 504, 403])('%i -> server, retryable', (s) => {
    expect(errorFromHttp(s)).toEqual({ code: 'server', retryable: true, action: null })
  })
})

describe('errorFromThrow', () => {
  const named = (name: string, extra: object = {}) => Object.assign(new Error(name), { name }, extra)

  it('abort is not an error', () => {
    expect(errorFromThrow(named('AbortError'))).toBeNull()
  })
  it('network failure (fetch TypeError) -> network', () => {
    expect(errorFromThrow(new TypeError('Failed to fetch'))?.code).toBe('network')
  })
  it('StreamInterruptedError -> interrupted', () => {
    expect(errorFromThrow(named('StreamInterruptedError'))?.code).toBe('interrupted')
  })
  it('watchdog -> timeout', () => {
    expect(errorFromThrow(named('StreamTimeoutError'))?.code).toBe('timeout')
  })
  it('HttpStatusError goes through errorFromHttp', () => {
    expect(errorFromThrow(named('HttpStatusError', { status: 401 }))?.code).toBe('session_expired')
    expect(errorFromThrow(named('HttpStatusError', { status: 503 }))?.code).toBe('server')
  })
  it('anything else is treated as a cut stream', () => {
    expect(errorFromThrow(new Error('boom'))?.code).toBe('interrupted')
  })
})

describe('action, retry and recovery rules', () => {
  const billing = errorFromEvent({ code: 'provider_billing' })

  it('a BYOK failure with ECA failing too points at the key settings; a code action wins', () => {
    expect(errorAction(billing, true)).toBe('llm_settings')
    expect(errorAction(billing, false)).toBeNull()
    expect(errorAction(errorFromHttp(401), true)).toBe('sign_in')
  })

  it('retry shows only on a retryable error, newest turn, not mid-stream', () => {
    expect(showRetry(billing, true, false)).toBe(true)
    expect(showRetry(billing, false, false)).toBe(false)
    expect(showRetry(billing, true, true)).toBe(false)
    expect(showRetry(errorFromHttp(400), true, false)).toBe(false)
  })

  it('recovery polling only for interrupted and network', () => {
    for (const code of [...BACKEND_CODES, ...FRONTEND_CODES]) {
      expect(shouldRecover(errorFromEvent({ code }))).toBe(code === 'interrupted' || code === 'network')
    }
  })
})

describe('errorFromHistory', () => {
  it('an error_code row is an error', () => {
    expect(errorFromHistory({ error_code: 'provider_billing' })).toEqual({
      code: 'provider_billing',
      retryable: true,
      action: null,
    })
  })
  it('a plain row has no error', () => {
    expect(errorFromHistory({})).toBeUndefined()
    expect(errorFromHistory({ error_code: null })).toBeUndefined()
  })
})
