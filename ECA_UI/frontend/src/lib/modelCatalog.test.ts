import { describe, expect, it } from 'vitest'
import { lookupModel, normalizeModelId, suggestModel } from './modelCatalog'

const CATALOG = {
  openai: {
    models: {
      'gpt-4o': {
        modalities: { input: ['text'], output: ['text'] },
        tool_call: true,
        temperature: true,
        cost: { input: 5 },
        release_date: '2024-05-01',
      },
      'gpt-4o-mini': {
        modalities: { input: ['text'], output: ['text'] },
        tool_call: true,
        temperature: true,
        cost: { input: 0.15 },
        release_date: '2024-07-01',
      },
      'o1-no-tools': {
        modalities: { input: ['text'], output: ['text'] },
        tool_call: false,
        temperature: false,
        cost: { input: 15 },
        release_date: '2024-12-01',
      },
    },
  },
  google: {
    models: {
      'gemini-2.5-flash': {
        id: 'gemini-2.5-flash',
        modalities: { input: ['text', 'image'], output: ['text'] },
        tool_call: true,
        temperature: true,
        cost: { input: 0.3 },
        release_date: '2025-01-01',
      },
    },
  },
}

describe('lookupModel', () => {
  it('finds a model inside its catalogId provider', () => {
    const meta = lookupModel(CATALOG, 'gemini-2.5-flash', 'google')
    expect(meta).toMatchObject({ textOutput: true, image: true, tools: true, temperature: true })
  })

  it('falls back to a global id match without a catalogId', () => {
    const meta = lookupModel(CATALOG, 'gpt-4o')
    expect(meta).toMatchObject({ textOutput: true, image: false, tools: true })
  })

  it('strips a models/ prefix before matching', () => {
    expect(normalizeModelId('models/gemini-2.5-flash')).toBe('gemini-2.5-flash')
    expect(lookupModel(CATALOG, 'models/gpt-4o')).not.toBeNull()
  })

  it('returns null for unknown ids and null catalogs', () => {
    expect(lookupModel(CATALOG, 'nope-9')).toBeNull()
    expect(lookupModel(null, 'gpt-4o')).toBeNull()
  })

  it('maps temperature:false through', () => {
    expect(lookupModel(CATALOG, 'o1-no-tools')?.temperature).toBe(false)
  })
})

describe('suggestModel', () => {
  it('picks the cheapest of the 5 newest tool-capable models', () => {
    const catalog = {
      p: {
        models: Object.fromEntries(
          ['m1', 'm2', 'm3', 'm4', 'm5', 'm6'].map((id, i) => [
            id,
            {
              modalities: { input: ['text'], output: ['text'] },
              tool_call: true,
              temperature: true,
              cost: { input: [0.01, 5, 4, 3, 2, 1][i] },
              release_date: `2024-0${i + 1}-01`,
            },
          ]),
        ),
      },
    }
    // Newest 5 are m2..m6 (m1 is the oldest, however cheap); cheapest of those is m6.
    expect(suggestModel(catalog, ['m1', 'm2', 'm3', 'm4', 'm5', 'm6'], 'p')).toBe('m6')
  })

  it('skips models without tools', () => {
    expect(suggestModel(CATALOG, ['o1-no-tools', 'gpt-4o-mini'], 'openai')).toBe('gpt-4o-mini')
  })

  it('falls back to the first id when nothing resolves', () => {
    expect(suggestModel(null, ['custom-a', 'custom-b'])).toBe('custom-a')
    expect(suggestModel(CATALOG, ['unknown-1'])).toBe('unknown-1')
  })
})
