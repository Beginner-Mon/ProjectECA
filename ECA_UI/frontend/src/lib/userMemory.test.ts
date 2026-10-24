import { describe, expect, it } from 'vitest'

import { MAX_FACT_LENGTH, groupByType, normaliseFacts, sortFacts, validateNewFact, validateTopic } from './userMemory'

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

describe('normaliseFact memory_type', () => {
  it('keeps memory_type and a null project', () => {
    const [fact] = normaliseFacts({ facts: [raw({ memory_type: 'person', project: null })] })
    expect(fact.memory_type).toBe('person')
    expect(fact.project).toBeNull()
  })

  it('derives it from kind when missing', () => {
    expect(normaliseFacts({ facts: [raw({ kind: 'instruction' })] })[0].memory_type).toBe('instruction')
    expect(normaliseFacts({ facts: [raw({ kind: 'fact' })] })[0].memory_type).toBe('profile')
    expect(normaliseFacts({ facts: [raw()] })[0].memory_type).toBe('profile')
  })

  it('derives it when memory_type is unknown', () => {
    expect(normaliseFacts({ facts: [raw({ memory_type: 'robot' })] })[0].memory_type).toBe('profile')
  })
})

describe('groupByType', () => {
  const item = (over: Record<string, unknown> = {}) => normaliseFacts({ facts: [raw(over)] })[0]

  it('orders sections in the fixed order and leaves empty ones out', () => {
    const groups = groupByType([
      item({ id: 'r', memory_type: 'reference' }),
      item({ id: 'p', memory_type: 'profile' }),
      item({ id: 'i', memory_type: 'instruction', kind: 'instruction' }),
      item({ id: 'e', memory_type: 'event' }),
    ])
    expect(groups.map((g) => g.type)).toEqual(['instruction', 'profile', 'event', 'reference'])
  })

  it('returns nothing for no facts', () => {
    expect(groupByType([])).toEqual([])
  })

  it('sorts topics alphabetically with no topic last', () => {
    const [group] = groupByType([
      item({ id: 'n' }),
      item({ id: 'z', project: 'work' }),
      item({ id: 'a', project: 'health' }),
    ])
    expect(group.topics.map((t) => [t.topic, t.items.map((f) => f.id)])).toEqual([
      ['health', ['a']], ['work', ['z']], [null, ['n']],
    ])
  })

  it('puts the newest first inside a topic', () => {
    const [group] = groupByType([
      item({ id: 'old', project: 'x', updated_at: '2026-10-01T00:00:00' }),
      item({ id: 'new', project: 'x', updated_at: '2026-10-09T00:00:00' }),
    ])
    expect(group.topics[0].items.map((f) => f.id)).toEqual(['new', 'old'])
  })

  it('keeps a tier-2 instruction in the instruction section under its topic', () => {
    const groups = groupByType([item({ id: 'x', memory_type: 'instruction', kind: 'instruction', tier: 2, project: 'run' })])
    expect(groups).toHaveLength(1)
    expect(groups[0].type).toBe('instruction')
    expect(groups[0].topics[0].topic).toBe('run')
    expect(groups[0].topics[0].items[0].tier).toBe(2)
  })

  it('shows a candidate row from an old backend in its type section', () => {
    const groups = groupByType([item({ id: 'c', status: 'candidate', memory_type: 'area' })])
    expect(groups.map((g) => g.type)).toEqual(['area'])
    expect(groups[0].topics[0].items.map((f) => f.id)).toEqual(['c'])
  })
})

describe('validateTopic', () => {
  it('returns null for no topic', () => {
    expect(validateTopic('')).toBeNull()
    expect(validateTopic('   ')).toBeNull()
  })
  it('trims and collapses spaces', () => {
    expect(validateTopic('  knee   rehab ')).toBe('knee rehab')
  })
  it('rejects more than 4 words', () => {
    expect(validateTopic('a b c d')).toBe('a b c d')
    expect(validateTopic('a b c d e')).toBe('invalid')
  })
  it('rejects more than 60 characters', () => {
    expect(validateTopic('a'.repeat(60))).toHaveLength(60)
    expect(validateTopic('a'.repeat(61))).toBe('invalid')
  })
})
