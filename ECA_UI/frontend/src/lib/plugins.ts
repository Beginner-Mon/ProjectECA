import type { PluginInfo } from './api'

/** The statuses the backend reports; anything else reads as 'unknown'. */
const KNOWN_STATUSES = [
  'active',
  'disabled',
  'off',
  'unconfigured',
  'definition_changed',
  'unavailable',
  'web_search_off',
] as const

/** The i18n key suffix for a status: `plugins.status_<suffix>`. */
export function statusKey(status: string): string {
  return (KNOWN_STATUSES as readonly string[]).includes(status) ? status : 'unknown'
}

/** The list with one plugin replaced by the server's answer; the rest are untouched. */
export function replacePlugin(list: PluginInfo[], updated: PluginInfo): PluginInfo[] {
  return list.map((p) => (p.id === updated.id ? updated : p))
}
