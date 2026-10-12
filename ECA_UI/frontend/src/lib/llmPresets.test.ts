import { describe, expect, it } from 'vitest'
import { LLM_PRESETS, providerList } from './llmPresets'

describe('llmPresets', () => {
  it('has no duplicate ids', () => {
    const ids = LLM_PRESETS.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('gives every non-local preset an https address (or blank for custom)', () => {
    for (const preset of LLM_PRESETS) {
      if (preset.local) continue
      expect(
        preset.baseUrl === '' || preset.baseUrl.startsWith('https://'),
        `${preset.id} must be https:// or blank`,
      ).toBe(true)
    }
  })

  it('keeps local presets on http localhost behind the local flag', () => {
    const locals = LLM_PRESETS.filter((p) => p.local)
    expect(locals.length).toBeGreaterThan(0)
    for (const preset of locals) {
      expect(preset.baseUrl.startsWith('http://localhost')).toBe(true)
    }
  })

  it('ships an OpenAI-Compatible blank preset for custom endpoints, first in line', () => {
    expect(LLM_PRESETS[0]).toMatchObject({ id: 'openai-compatible', baseUrl: '' })
  })
})

const CHAT = '@ai-sdk/openai-compatible'

describe('providerList', () => {
  it('is the hand-written entries alone when there is no catalog', () => {
    expect(providerList(null)).toEqual(LLM_PRESETS)
  })

  it('adds the catalog providers after the hand-written ones, pointing at their own catalog entry', () => {
    const list = providerList({
      newcomer: { name: 'Newcomer', api: 'https://api.newcomer.example/v1', npm: CHAT, models: {} },
    })
    expect(list.slice(0, LLM_PRESETS.length)).toEqual(LLM_PRESETS)
    expect(list.slice(LLM_PRESETS.length)).toEqual([
      { id: 'newcomer', name: 'Newcomer', baseUrl: 'https://api.newcomer.example/v1', catalogId: 'newcomer' },
    ])
  })

  it('does not repeat a provider that is already written by hand', () => {
    // Should the catalog start recording an address for these two, the cards
    // must not double up: one shares its id with a preset (groq), the other is
    // a preset's catalogId under a different preset id (gemini → google).
    const list = providerList({
      groq: { name: 'Groq', api: 'https://api.groq.com/openai/v1', npm: CHAT, models: {} },
      google: { name: 'Google', api: 'https://generativelanguage.googleapis.com/v1beta/openai', npm: CHAT, models: {} },
    })
    expect(list).toEqual(LLM_PRESETS)
  })
})
