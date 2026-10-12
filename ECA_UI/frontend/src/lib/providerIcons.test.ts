import { describe, expect, it } from 'vitest'
import { LLM_PRESETS } from './llmPresets'
import { providerIconIds, providerIconUrl } from './providerIcons'

// Presets the icon set has no mark for. Listed, not inferred: a preset added
// later without a logo file should fail here and make someone decide, rather
// than quietly ship the neutral box.
const NO_LOGO = new Set(['openai-compatible'])

describe('provider icons', () => {
  it('has a logo for every preset except the known few', () => {
    const missing = LLM_PRESETS.map((p) => p.id).filter((id) => !NO_LOGO.has(id) && !providerIconUrl(id))
    expect(missing).toEqual([])
  })

  it('has no logo file that belongs to no preset', () => {
    const presetIds = new Set(LLM_PRESETS.map((p) => p.id))
    expect(providerIconIds().filter((id) => !presetIds.has(id))).toEqual([])
  })

  it('gives no logo to a preset that has none of its own', () => {
    for (const id of NO_LOGO) expect(providerIconUrl(id)).toBeUndefined()
  })
})
