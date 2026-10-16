import { describe, expect, it } from 'vitest'

import { MAX_FACT_LENGTH, groupFacts, normaliseFacts, sortFacts, validateNewFact } from './userMemory'

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
  it('counts Unicode characters like the backend, rather than UTF-16 units', () => {
    expect(validateNewFact('😀'.repeat(MAX_FACT_LENGTH))).not.toBeNull()
    expect(validateNewFact('😀'.repeat(MAX_FACT_LENGTH + 1))).toBeNull()
  })
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

describe('normaliseFact tier defaults', () => {
  it('fills fact/2/active/null/created_at for an older backend', () => {
    const [fact] = normaliseFacts({ facts: [raw()] })
    expect(fact.kind).toBe('fact')
    expect(fact.tier).toBe(2)
    expect(fact.status).toBe('active')
    expect(fact.project).toBeNull()
    expect(fact.said_at).toBe('2026-10-01T09:00:00')
  })

  it('keeps tier fields when present', () => {
    const [fact] = normaliseFacts({ facts: [raw({
      kind: 'instruction', tier: 1, status: 'candidate', project: 'run',
      said_at: '2026-10-09T00:00:00',
    })] })
    expect(fact.kind).toBe('instruction')
    expect(fact.tier).toBe(1)
    expect(fact.status).toBe('candidate')
    expect(fact.project).toBe('run')
    expect(fact.said_at).toBe('2026-10-09T00:00:00')
  })
})

describe('groupFacts', () => {
  const item = (over: Record<string, unknown> = {}) => normaliseFacts({ facts: [raw(over)] })[0]

  it('splits instructions, project groups and candidates', () => {
    const groups = groupFacts([
      item({ id: 'i', kind: 'instruction', tier: 1 }),
      item({ id: 'p', project: 'run' }),
      item({ id: 'n' }),
      item({ id: 'c', status: 'candidate' }),
    ])
    expect(groups.instructions.map((f) => f.id)).toEqual(['i'])
    expect(groups.byProject.map((g) => [g.project, g.items.map((f) => f.id)])).toEqual([
      ['run', ['p']],
      [null, ['n']],
    ])
    expect(groups.candidates.map((f) => f.id)).toEqual(['c'])
  })

  it('keeps tier-2 instructions out of the standing section', () => {
    const groups = groupFacts([item({ id: 'x', kind: 'instruction', tier: 2, project: 'run' })])
    expect(groups.instructions).toEqual([])
    expect(groups.byProject).toHaveLength(1)
  })

  it('keeps tier-1 facts in the standing section regardless of kind', () => {
    const groups = groupFacts([item({ id: 'f', kind: 'fact', tier: 1, fact_text: 'Has back pain' })])
    expect(groups.instructions.map((f) => f.id)).toEqual(['f'])
    expect(groups.byProject).toEqual([])
  })
})
