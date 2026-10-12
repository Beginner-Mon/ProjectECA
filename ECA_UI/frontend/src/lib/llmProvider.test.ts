import { describe, expect, it, beforeEach, vi } from 'vitest'
import {
  activeLlmChoice,
  clearAllLlmState,
  defaultLlmState,
  loadLlmState,
  saveLlmState,
  type LlmState,
} from './llmProvider'

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial))
  return {
    get length() {
      return map.size
    },
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => map.clear(),
    _map: map,
  }
}

let local: ReturnType<typeof fakeStorage>
let session: ReturnType<typeof fakeStorage>

beforeEach(() => {
  local = fakeStorage()
  session = fakeStorage()
  vi.stubGlobal('localStorage', local)
  vi.stubGlobal('sessionStorage', session)
})

describe('loadLlmState / saveLlmState', () => {
  it('returns the default when nothing is stored', () => {
    expect(loadLlmState('sub-1')).toEqual({ active: 'eca', connections: {} })
  })

  it('round-trips per sub without leaking across accounts', () => {
    const state: LlmState = {
      active: 'openai',
      connections: {
        openai: {
          presetId: 'openai',
          name: 'OpenAI',
          baseUrl: 'https://api.openai.com/v1',
          apiKey: 'k-a',
          model: 'gpt-4o',
          capabilities: ['text', 'answer', 'tools'],
          omit: [],
          status: 'configured',
        },
      },
    }
    saveLlmState('sub-a', state)
    expect(loadLlmState('sub-a')).toEqual(state)
    expect(loadLlmState('sub-b')).toEqual(defaultLlmState())
  })

  it('writes nothing when there is no account (demo mode)', () => {
    expect(loadLlmState(null)).toEqual(defaultLlmState())
    saveLlmState(null, {
      active: 'openai',
      connections: {},
    })
    expect(local._map.size).toBe(0)
  })

  it('falls back to default on corrupt JSON instead of throwing', () => {
    local.setItem('eca-llm-sub-x', '{not json')
    expect(loadLlmState('sub-x')).toEqual(defaultLlmState())
  })
})

describe('clearAllLlmState', () => {
  it('removes every eca-llm-* key and keeps the rest', () => {
    local.setItem('eca-llm-a', '{}')
    local.setItem('eca-llm-b', '{}')
    local.setItem('eca-demo-user', 'keep')
    session.setItem('eca-llm-a', '{}')
    clearAllLlmState()
    expect(local.getItem('eca-llm-a')).toBeNull()
    expect(local.getItem('eca-llm-b')).toBeNull()
    expect(local.getItem('eca-demo-user')).toBe('keep')
    expect(session.getItem('eca-llm-a')).toBeNull()
  })
})

describe('activeLlmChoice', () => {
  const base = {
    presetId: 'openai',
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    apiKey: 'k',
    model: 'gpt-4o',
    capabilities: ['text', 'answer', 'tools'] as const,
    omit: [] as const,
  }

  it('returns null when ECA is active', () => {
    expect(activeLlmChoice({ active: 'eca', connections: { openai: { ...base, status: 'configured' } } })).toBeNull()
  })

  it('returns the choice for configured and unverified', () => {
    for (const status of ['configured', 'unverified'] as const) {
      const choice = activeLlmChoice({
        active: 'openai',
        connections: { openai: { ...base, status } },
      })
      expect(choice).toMatchObject({
        base_url: 'https://api.openai.com/v1',
        model: 'gpt-4o',
        api_key: 'k',
      })
    }
  })

  it('returns null for unconfigured, validating and invalid', () => {
    for (const status of ['unconfigured', 'validating', 'invalid'] as const) {
      expect(
        activeLlmChoice({ active: 'openai', connections: { openai: { ...base, status } } }),
      ).toBeNull()
    }
  })

  it('returns null when the connection is missing or has no model', () => {
    expect(activeLlmChoice({ active: 'openai', connections: {} })).toBeNull()
    expect(
      activeLlmChoice({
        active: 'openai',
        connections: { openai: { ...base, status: 'configured', model: '' } },
      }),
    ).toBeNull()
  })
})
