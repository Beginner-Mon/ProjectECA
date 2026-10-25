import { describe, expect, it } from 'vitest'
import { sessionLoadOutcome } from './sessionLoad'

describe('sessionLoadOutcome', () => {
  it('abort and axios cancel are cancelled, not failures', () => {
    expect(sessionLoadOutcome({ name: 'AbortError' })).toBe('cancelled')
    expect(sessionLoadOutcome({ code: 'ERR_CANCELED' })).toBe('cancelled')
  })
  it('404 is missing', () => {
    expect(sessionLoadOutcome({ response: { status: 404 } })).toBe('missing')
  })
  it.each([401, 500, 503])('HTTP %i is a failure', (status) => {
    expect(sessionLoadOutcome({ response: { status } })).toBe('failed')
  })
  it('a network error with no response is a failure', () => {
    expect(sessionLoadOutcome(new Error('Network Error'))).toBe('failed')
    expect(sessionLoadOutcome(null)).toBe('failed')
  })
})
