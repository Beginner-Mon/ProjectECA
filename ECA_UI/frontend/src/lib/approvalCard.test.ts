import { describe, expect, it } from 'vitest'
import {
  canAnswer,
  isExpired,
  msUntilExpiry,
  parsePayload,
  provenanceLabels,
  replyIsPlainText,
  stateAfterTurn,
  stateFromPending,
  type PendingAction,
} from './approvalCard'

const wire = {
  thread_id: 't1',
  action_id: 'a1',
  tool: 'create_reminder',
  plugin_id: 'dev-reminders',
  display: [{ label: 'Title', value: 'Walk' }],
  recipients: ['x@y.com'],
  provenance: [
    { tool: 'kb_search', source_label: 'Knowledge base' },
    { tool: 'kb_search', source_label: 'Knowledge base' },
  ],
  expires_at: '2026-11-02T10:30:00+00:00',
}

describe('parsePayload', () => {
  it('reads the server payload', () => {
    expect(parsePayload(wire)).toEqual(wire)
  })

  it('turns every value into a plain string', () => {
    const p = parsePayload({ ...wire, display: [{ label: 'n', value: 5 }, null], recipients: [1, 'a'] })!
    expect(p.display).toEqual([{ label: 'n', value: '5' }, { label: '', value: '' }])
    expect(p.recipients).toEqual(['1', 'a'])
  })

  it('refuses a payload that cannot be answered', () => {
    expect(parsePayload(null)).toBeNull()
    expect(parsePayload({ ...wire, action_id: '' })).toBeNull()
    expect(parsePayload({ ...wire, thread_id: undefined })).toBeNull()
  })

  it('survives missing lists', () => {
    const p = parsePayload({ thread_id: 't', action_id: 'a' })!
    expect(p.display).toEqual([])
    expect(p.recipients).toEqual([])
    expect(p.provenance).toEqual([])
  })
})

describe('provenanceLabels', () => {
  it('lists each source once', () => {
    expect(provenanceLabels(parsePayload(wire)!)).toEqual(['Knowledge base'])
  })
})

describe('expiry', () => {
  const p = parsePayload(wire)!
  const at = Date.parse(wire.expires_at)
  it('is not expired before the deadline and is at it', () => {
    expect(isExpired(p, at - 1)).toBe(false)
    expect(isExpired(p, at)).toBe(true)
  })
  it('counts down and never goes negative', () => {
    expect(msUntilExpiry(p, at - 5000)).toBe(5000)
    expect(msUntilExpiry(p, at + 5000)).toBe(0)
  })
  it('leaves an unreadable deadline to the server', () => {
    const q = { ...p, expires_at: 'soon' }
    expect(isExpired(q, Date.now())).toBe(false)
    expect(msUntilExpiry(q, Date.now())).toBeNull()
  })
})

describe('state helpers', () => {
  it('offers the buttons only while the card can be answered', () => {
    expect(canAnswer('pending')).toBe(true)
    expect(canAnswer('error')).toBe(true)
    for (const s of ['approving', 'done', 'declined', 'expired', 'invalidated'] as const) {
      expect(canAnswer(s)).toBe(false)
    }
  })

  it('reads the reply as markdown only once the card is settled', () => {
    expect(replyIsPlainText('pending')).toBe(true)
    expect(replyIsPlainText('invalidated')).toBe(true)
    expect(replyIsPlainText('done')).toBe(false)
    expect(replyIsPlainText('declined')).toBe(false)
  })
})

describe('stateAfterTurn', () => {
  const act = (status: string) => [{ action_id: 'a1', status }, { action_id: 'other', status: 'failed' }]
  it.each([
    ['succeeded', 'done'],
    ['failed', 'done'],
    ['unknown', 'done'],
    ['declined', 'declined'],
    ['expired', 'expired'],
    ['invalidated', 'invalidated'],
  ])('maps %s to %s', (status, state) => {
    expect(stateAfterTurn('a1', 'approve', act(status))).toBe(state)
  })

  it('falls back to the choice the user made when the action is not listed', () => {
    expect(stateAfterTurn('a1', 'approve', null)).toBe('done')
    expect(stateAfterTurn('a1', 'decline', [])).toBe('declined')
  })
})

describe('stateFromPending', () => {
  const row = (status: string): PendingAction => ({ ...parsePayload(wire)!, status })
  it('is answerable again when still listed as proposed', () => {
    expect(stateFromPending('a1', [row('proposed')])).toBe('pending')
  })
  it('is expired when listed as expired', () => {
    expect(stateFromPending('a1', [row('expired')])).toBe('expired')
  })
  it('is invalidated when absent', () => {
    expect(stateFromPending('a1', [])).toBe('invalidated')
    expect(stateFromPending('a1', null)).toBe('invalidated')
  })
})
