import { describe, expect, it } from 'vitest'
import en from '../i18n/locales/en.json'
import vi from '../i18n/locales/vi.json'
import type { PluginInfo } from './api'
import { replacePlugin, statusKey } from './plugins'

const plugin = (id: string, enabled = false, status = 'disabled'): PluginInfo => ({
  id, name: id, publisher: 'p', description: 'd', enabled, status,
})

const STATUSES = ['active', 'disabled', 'off', 'unconfigured', 'definition_changed', 'unavailable', 'web_search_off']

describe('statusKey', () => {
  it('keeps every status the backend reports', () => {
    for (const s of STATUSES) expect(statusKey(s)).toBe(s)
  })

  it('maps anything else to unknown', () => {
    expect(statusKey('brand_new_status')).toBe('unknown')
    expect(statusKey('')).toBe('unknown')
  })

  it('has a label in both languages for every status', () => {
    for (const s of [...STATUSES, 'unknown']) {
      const key = `status_${statusKey(s)}`
      expect((en.plugins as Record<string, string>)[key], `en ${key}`).toBeTruthy()
      expect((vi.plugins as Record<string, string>)[key], `vi ${key}`).toBeTruthy()
    }
  })
})

describe('replacePlugin', () => {
  it('swaps only the matching plugin and does not mutate the input', () => {
    const list = [plugin('a'), plugin('b')]
    const next = replacePlugin(list, plugin('b', true, 'active'))
    expect(next.map((p) => [p.id, p.enabled, p.status])).toEqual([['a', false, 'disabled'], ['b', true, 'active']])
    expect(next[0]).toBe(list[0])
    expect(list[1].enabled).toBe(false)
  })

  it('leaves the list alone for an unknown id', () => {
    const list = [plugin('a')]
    expect(replacePlugin(list, plugin('zzz'))).toEqual(list)
  })
})
