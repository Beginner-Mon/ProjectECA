import { describe, expect, it } from 'vitest'

import { MAX_FACT_LENGTH, normaliseFacts, sortFacts, validateNewFact } from './userMemory'

const raw = (over: Record<string, unknown> = {}) => ({
  id: 'f1',
  fact_text: 'prefers short answers',
  category: null,
  created_at: '2026-10-01T09:00:00',
  ...over,
})

describe('normaliseFacts', () => {
  it('treats a missing source as typed by the user', () => {
    expect(normaliseFacts({ facts: [raw()] })[0].source).toBe('user')
  })

  it('treats an unknown source as typed by the user', () => {
    expect(normaliseFacts({ facts: [raw({ source: 'robot' })] })[0].source).toBe('user')
  })

  it('keeps extracted', () => {
    expect(normaliseFacts({ facts: [raw({ source: 'extracted' })] })[0].source).toBe('extracted')
  })

  it('falls back to created_at when updated_at is missing or null', () => {
    expect(normaliseFacts({ facts: [raw()] })[0].updated_at).toBe('2026-10-01T09:00:00')
    expect(normaliseFacts({ facts: [raw({ updated_at: null })] })[0].updated_at).toBe('2026-10-01T09:00:00')
  })

  it('keeps updated_at when present', () => {
    expect(normaliseFacts({ facts: [raw({ updated_at: '2026-10-05T09:00:00' })] })[0].updated_at).toBe('2026-10-05T09:00:00')
  })

  it('returns an empty list for a malformed answer', () => {
    expect(normaliseFacts(undefined)).toEqual([])
    expect(normaliseFacts({})).toEqual([])
    expect(normaliseFacts({ facts: 'x' })).toEqual([])
  })
})

describe('validateNewFact', () => {
  it('trims', () => {
    expect(validateNewFact('  knee pain  ')).toBe('knee pain')
  })

  it('rejects empty and whitespace-only text', () => {
    expect(validateNewFact('')).toBeNull()
    expect(validateNewFact('   \n ')).toBeNull()
  })

  it('accepts exactly the limit and rejects one over', () => {
    expect(validateNewFact('a'.repeat(MAX_FACT_LENGTH))).toHaveLength(MAX_FACT_LENGTH)
    expect(validateNewFact('a'.repeat(MAX_FACT_LENGTH + 1))).toBeNull()
  })

  it('counts the trimmed text, not the raw input', () => {
    expect(validateNewFact(' ' + 'a'.repeat(MAX_FACT_LENGTH) + ' ')).not.toBeNull()
  })
})

describe('sortFacts', () => {
  it('puts the most recently updated first, without changing the input', () => {
    const a = normaliseFacts({ facts: [raw({ id: 'a', updated_at: '2026-10-01T00:00:00' })] })[0]
    const b = normaliseFacts({ facts: [raw({ id: 'b', updated_at: '2026-10-09T00:00:00' })] })[0]
    const input = [a, b]
    expect(sortFacts(input).map((f) => f.id)).toEqual(['b', 'a'])
    expect(input.map((f) => f.id)).toEqual(['a', 'b'])
  })
})
