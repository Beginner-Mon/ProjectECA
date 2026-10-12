import { describe, expect, it } from 'vitest'
import { LLM_PRESETS } from './llmPresets'

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

  it('ships an OpenAI-Compatible blank preset for custom endpoints', () => {
    const custom = LLM_PRESETS.find((p) => p.id === 'openai-compatible')
    expect(custom).toBeDefined()
    expect(custom?.baseUrl).toBe('')
  })
})
