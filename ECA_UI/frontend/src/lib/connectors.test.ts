import { describe, expect, it } from 'vitest'
import { controlFor, parseCallbackQuery } from './connectors'

describe('parseCallbackQuery', () => {
  it('reads code, state and iss from the query string', () => {
    expect(parseCallbackQuery('?code=c1&state=s1&iss=https://auth.example.com'))
      .toEqual({ code: 'c1', state: 's1', iss: 'https://auth.example.com' })
  })

  it('leaves iss empty when it is absent', () => {
    expect(parseCallbackQuery('?code=c1&state=s1')).toEqual({ code: 'c1', state: 's1', iss: '' })
  })

  it('returns missing_parameter without calling the API when code is absent', () => {
    expect(parseCallbackQuery('?state=s1')).toEqual({ error: 'missing_parameter' })
  })

  it('returns missing_parameter when state is absent', () => {
    expect(parseCallbackQuery('?code=c1')).toEqual({ error: 'missing_parameter' })
  })

  it('returns missing_parameter when both are absent', () => {
    expect(parseCallbackQuery('?')).toEqual({ error: 'missing_parameter' })
    expect(parseCallbackQuery('')).toEqual({ error: 'missing_parameter' })
  })
})

describe('controlFor', () => {
  it('offers Connect when there is no connection', () => {
    expect(controlFor(null)).toBe('connect')
  })

  it('offers Disconnect when the connection is active', () => {
    expect(controlFor({ plugin_id: 'gmail', status: 'active', scopes: [], expires_at: null }))
      .toBe('disconnect')
  })

  it('offers Reconnect when the token expired', () => {
    expect(controlFor({ plugin_id: 'gmail', status: 'auth_expired', scopes: [], expires_at: null }))
      .toBe('reconnect')
  })

  it('offers Connect for any other status', () => {
    expect(controlFor({ plugin_id: 'gmail', status: 'off', scopes: [], expires_at: null })).toBe('connect')
    expect(controlFor({ plugin_id: 'gmail', status: 'unconfigured', scopes: [], expires_at: null })).toBe('connect')
  })
})
