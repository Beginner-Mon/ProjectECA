import { describe, expect, it } from 'vitest'
import {
  catalogIdForBaseUrl,
  catalogProviders,
  groupModels,
  lookupModel,
  normalizeModelId,
  suggestModel,
} from './modelCatalog'

// A gateway: one key, many models, not all served over chat completions.
// Shaped like models.dev's entry for OpenCode Zen.
const GATEWAY = {
  gateway: {
    api: 'https://gateway.example/zen/v1',
    npm: '@ai-sdk/openai-compatible',
    models: {
      'plain-chat': {
        modalities: { input: ['text'], output: ['text'] },
        tool_call: true,
        cost: { input: 1 },
        release_date: '2025-01-01',
      },
      // Free and newest — the natural default, and unusable: Responses format.
      'free-responses': {
        modalities: { input: ['text'], output: ['text'] },
        tool_call: true,
        cost: { input: 0 },
        release_date: '2026-01-01',
        provider: { npm: '@ai-sdk/openai' },
      },
      'claude-like': {
        modalities: { input: ['text'], output: ['text'] },
        tool_call: true,
        cost: { input: 3 },
        release_date: '2025-06-01',
        provider: { npm: '@ai-sdk/anthropic' },
      },
    },
  },
}

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

describe('groupModels', () => {
  it('puts chat-capable models first, newest first, and keeps the rest in provider order', () => {
    const groups = groupModels(
      CATALOG,
      ['text-embedding-9', 'gpt-4o', 'o1-no-tools', 'gpt-4o-mini', 'whisper-9'],
      'openai',
    )
    // gpt-4o-mini (2024-07) is newer than gpt-4o (2024-05).
    expect(groups.recommended).toEqual(['gpt-4o-mini', 'gpt-4o'])
    // Unknown ids and the known model that cannot call tools: nothing is hidden.
    expect(groups.other).toEqual(['text-embedding-9', 'o1-no-tools', 'whisper-9'])
  })

  it('matches ids carrying the models/ prefix and returns them unchanged', () => {
    const groups = groupModels(CATALOG, ['models/gemini-2.5-flash', 'models/embedding-001'], 'google')
    expect(groups.recommended).toEqual(['models/gemini-2.5-flash'])
    expect(groups.other).toEqual(['models/embedding-001'])
  })

  it('finds a model outside the catalogId provider', () => {
    expect(groupModels(CATALOG, ['gpt-4o'], 'google').recommended).toEqual(['gpt-4o'])
  })

  it('lists everything as other when there is no catalog', () => {
    expect(groupModels(null, ['a', 'b'])).toEqual({ recommended: [], other: ['a', 'b'], unsupported: [] })
  })

  it('sets aside models the provider serves over another API', () => {
    const groups = groupModels(GATEWAY, ['free-responses', 'plain-chat', 'claude-like', 'brand-new'], 'gateway')
    expect(groups.recommended).toEqual(['plain-chat'])
    expect(groups.other).toEqual(['brand-new']) // unknown: may work, stays selectable
    expect(groups.unsupported).toEqual(['free-responses', 'claude-like'])
  })
})

describe('models served over another API', () => {
  it('marks a per-model adapter override as not chat, and nothing else', () => {
    expect(lookupModel(GATEWAY, 'plain-chat', 'gateway')?.chat).toBe(true)
    expect(lookupModel(GATEWAY, 'free-responses', 'gateway')?.chat).toBe(false)
    // No per-model override: usable, whatever the provider's own default SDK.
    expect(lookupModel(CATALOG, 'gpt-4o', 'openai')?.chat).toBe(true)
  })

  it('never suggests one, however cheap and new', () => {
    expect(suggestModel(GATEWAY, ['free-responses', 'claude-like', 'plain-chat'], 'gateway')).toBe('plain-chat')
  })

  it('suggests nothing when every listed model is unusable', () => {
    expect(suggestModel(GATEWAY, ['free-responses', 'claude-like'], 'gateway')).toBeUndefined()
  })

  it('falls back to an unknown id rather than a known-unusable one', () => {
    expect(suggestModel(GATEWAY, ['free-responses', 'brand-new'], 'gateway')).toBe('brand-new')
  })
})

describe('the floor: text in, text out', () => {
  // One key lists all of these side by side; only two of them can hold a chat.
  const MIXED = {
    studio: {
      models: {
        'text-only': { modalities: { input: ['text'], output: ['text'] }, tool_call: true, release_date: '2025-01-01' },
        'text-and-image': {
          modalities: { input: ['text', 'image'], output: ['text'] },
          tool_call: true,
          release_date: '2025-02-01',
        },
        // Newest of all, so a careless default would land on it.
        'image-maker': { modalities: { input: ['text'], output: ['image'] }, tool_call: true, release_date: '2026-01-01' },
        'listener': { modalities: { input: ['audio'], output: ['text'] }, tool_call: true, release_date: '2026-02-01' },
      },
    },
  }
  const IDS = ['image-maker', 'listener', 'text-only', 'text-and-image']

  it('offers text and text+image models, and sets aside what cannot chat', () => {
    const groups = groupModels(MIXED, IDS, 'studio')
    expect(groups.recommended).toEqual(['text-and-image', 'text-only'])
    expect(groups.other).toEqual([])
    expect(groups.unsupported).toEqual(['image-maker', 'listener'])
  })

  it('never suggests a model that cannot chat', () => {
    expect(suggestModel(MIXED, IDS, 'studio')).toBe('text-and-image')
    expect(suggestModel(MIXED, ['image-maker', 'listener'], 'studio')).toBeUndefined()
  })

  it('records that a model takes images alongside text', () => {
    expect(lookupModel(MIXED, 'text-and-image', 'studio')?.image).toBe(true)
    expect(lookupModel(MIXED, 'text-only', 'studio')?.image).toBe(false)
  })
})

describe('catalogProviders', () => {
  const CHAT = '@ai-sdk/openai-compatible'
  const entry = (name: string, api: string | undefined, npm = CHAT, models: string[] = []) => ({
    name,
    ...(api ? { api } : {}),
    npm,
    models: Object.fromEntries(models.map((id) => [id, {}])),
  })

  it('lists what speaks chat completions at a fixed https address, by name', () => {
    const list = catalogProviders({
      zeta: entry('Zeta', 'https://api.zeta.example/v1'),
      alpha: entry('alpha.dev', 'https://api.alpha.example/v1'),
      beta: entry('Beta', 'https://api.beta.example/v1'),
    })
    expect(list).toEqual([
      { id: 'alpha', name: 'alpha.dev', baseUrl: 'https://api.alpha.example/v1' },
      { id: 'beta', name: 'Beta', baseUrl: 'https://api.beta.example/v1' },
      { id: 'zeta', name: 'Zeta', baseUrl: 'https://api.zeta.example/v1' },
    ])
  })

  it('leaves out what ECA cannot be pointed at as described', () => {
    const list = catalogProviders({
      'own-sdk': entry('Own SDK', undefined, '@ai-sdk/openai'), // no address at all
      'other-format': entry('Other Format', 'https://api.other.example/v1', '@ai-sdk/anthropic'),
      'fill-in': entry('Fill In', 'https://${ACCOUNT}.fill.example/v1'), // needs the user's account id
      'on-machine': entry('On Machine', 'http://127.0.0.1:1234/v1'),
    })
    expect(list).toEqual([])
  })

  it('leaves out coding-tool subscriptions and keeps the same company pay-per-use entry', () => {
    const list = catalogProviders({
      acme: entry('Acme', 'https://api.acme.example/v1'),
      'acme-coding-plan': entry('Acme Coding Plan', 'https://coding.acme.example/v1'),
      'acme-token-plan': entry('Acme Token Plan (China)', 'https://plan.acme.example/v1'),
      'acme-code': entry('Acme For Coding (acme.ai)', 'https://api.acme.example/coding/v1'),
      // "plan" inside another word is not a plan.
      planetary: entry('Planetary', 'https://api.planetary.example/v1'),
    })
    expect(list.map((p) => p.id)).toEqual(['acme', 'planetary'])
  })

  it('shows an address listed twice once, under the entry with more models', () => {
    const list = catalogProviders({
      'gw-pass': entry('Pass (Gateway)', 'https://api.gw.example/v1', CHAT, ['a']),
      gw: entry('Gateway', 'https://api.gw.example/v1/', CHAT, ['a', 'b', 'c']),
    })
    expect(list).toEqual([{ id: 'gw', name: 'Gateway', baseUrl: 'https://api.gw.example/v1/' }])
  })

  it('returns nothing without a catalog', () => {
    expect(catalogProviders(null)).toEqual([])
  })
})

describe('catalogIdForBaseUrl', () => {
  it('recognises a provider by its API address, ignoring case and trailing slashes', () => {
    expect(catalogIdForBaseUrl(GATEWAY, 'https://gateway.example/zen/v1')).toBe('gateway')
    expect(catalogIdForBaseUrl(GATEWAY, ' HTTPS://Gateway.example/zen/v1/ ')).toBe('gateway')
  })

  it('returns nothing for an unknown or half-typed address, or without a catalog', () => {
    expect(catalogIdForBaseUrl(GATEWAY, 'https://gateway.example/zen/v')).toBeUndefined()
    expect(catalogIdForBaseUrl(GATEWAY, '')).toBeUndefined()
    expect(catalogIdForBaseUrl(null, 'https://gateway.example/zen/v1')).toBeUndefined()
  })
})

describe('context window', () => {
  const withLimit = (limit: unknown) => ({
    p: {
      models: {
        m: { modalities: { input: ['text'], output: ['text'] }, tool_call: true, limit },
      },
    },
  })
  const windowOf = (limit: unknown) => lookupModel(withLimit(limit), 'm')?.contextWindow

  it('reads limit.context', () => {
    expect(windowOf({ context: 128000, output: 4096 })).toBe(128000)
  })

  it('leaves it out when absent, zero, negative, fractional or not a number', () => {
    for (const limit of [undefined, {}, { output: 4096 }, { context: 0 }, { context: -5 }, { context: 1.5 }, { context: '128000' }, { context: null }, { context: Infinity }, 'x']) {
      expect(windowOf(limit)).toBeUndefined()
    }
  })
})
