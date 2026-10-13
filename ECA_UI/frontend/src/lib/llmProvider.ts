/**
 * BYOK local state — the user's own LLM connections, kept in THIS browser only.
 *
 * localStorage key `eca-llm-<sub>`: the Cognito sub namespaces rows per account
 * (the same browser signing into another account must not inherit the first
 * one's key), and sign-out wipes every `eca-llm-*` key. Demo mode (no sub)
 * reads and writes nothing. A read failure falls back to the default state
 * rather than throwing — a corrupt row must not break the settings screen.
 */

import { cognitoSub } from './api'

export type LlmCapability = 'text' | 'answer' | 'tools' | 'image'
export type LlmOmit = 'temperature' | 'max_tokens'
export type LlmStatus = 'unconfigured' | 'validating' | 'configured' | 'unverified' | 'invalid'

export interface LlmConnection {
  presetId: string
  name: string
  baseUrl: string
  apiKey: string
  model: string
  capabilities: LlmCapability[]
  omit: LlmOmit[]
  status: LlmStatus
  /**
   * Only meaningful with status 'unverified' (no trial message could be sent
   * from the browser): the provider was seen to check keys, and took this one.
   */
  keyAccepted?: boolean
  /**
   * The model's context window in tokens, from the catalog at the time it was
   * checked. Absent on connections saved before it was kept.
   */
  contextWindow?: number
}

export interface LlmState {
  active: 'eca' | string
  connections: Record<string, LlmConnection>
}

export interface LlmChoice {
  base_url: string
  model: string
  api_key: string
  capabilities: LlmCapability[]
  omit: LlmOmit[]
}

const KEY_PREFIX = 'eca-llm-'

export function llmStorageKey(sub: string): string {
  return `${KEY_PREFIX}${sub}`
}

export function defaultLlmState(): LlmState {
  return { active: 'eca', connections: {} }
}

function isLlmState(value: unknown): value is LlmState {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  if (typeof v.active !== 'string') return false
  if (typeof v.connections !== 'object' || v.connections === null) return false
  return true
}

export function loadLlmState(sub: string | null): LlmState {
  if (!sub) return defaultLlmState()
  try {
    const raw = localStorage.getItem(llmStorageKey(sub))
    if (!raw) return defaultLlmState()
    const parsed: unknown = JSON.parse(raw)
    if (!isLlmState(parsed)) return defaultLlmState()
    return parsed
  } catch {
    return defaultLlmState()
  }
}

export async function loadLlmStateForCurrentUser(): Promise<{ sub: string | null; state: LlmState }> {
  const sub = await cognitoSub()
  return { sub, state: loadLlmState(sub) }
}

export function saveLlmState(sub: string | null, state: LlmState): void {
  if (!sub) return
  try {
    localStorage.setItem(llmStorageKey(sub), JSON.stringify(state))
  } catch {
    // Quota or privacy mode — the in-memory state still works for this page.
  }
}

export function clearAllLlmState(): void {
  try {
    const doomed: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith(KEY_PREFIX)) doomed.push(key)
    }
    doomed.forEach((key) => localStorage.removeItem(key))
  } catch {
    // Storage unavailable — nothing to clear.
  }
  try {
    const doomed: string[] = []
    for (let i = 0; i < sessionStorage.length; i++) {
      const key = sessionStorage.key(i)
      if (key && key.startsWith(KEY_PREFIX)) doomed.push(key)
    }
    doomed.forEach((key) => sessionStorage.removeItem(key))
  } catch {
    // ignore
  }
}

/**
 * The `llm` body for POST /chat, or null when the turn must run on ECA:
 * active is 'eca', no such connection, an empty address/model, or a status
 * that never validated (unconfigured / validating / invalid).
 */
export function activeLlmChoice(state: LlmState): LlmChoice | null {
  if (state.active === 'eca') return null
  const conn = state.connections[state.active]
  if (!conn) return null
  if (conn.status !== 'configured' && conn.status !== 'unverified') return null
  if (!conn.baseUrl.trim() || !conn.model.trim()) return null
  return {
    base_url: conn.baseUrl,
    model: conn.model,
    api_key: conn.apiKey,
    capabilities: conn.capabilities,
    omit: conn.omit,
  }
}

export function llmChoiceToSend(
  state: LlmState,
  flags: { byokEnabled: boolean; configLoading: boolean },
): LlmChoice | null {
  // Backend đã trả lời là không hỗ trợ → không gửi key đi.
  // Đang tải cấu hình → chưa biết, giữ hành vi hiện tại.
  if (!flags.byokEnabled && !flags.configLoading) return null
  return activeLlmChoice(state)
}

/**
 * The context window to send as the top-level `token_limit` of POST /chat:
 * only when the turn goes out on the user's own connection (same conditions as
 * `llmChoiceToSend`) and the window is known. Never part of the `llm` body —
 * the backend rejects unknown fields there.
 */
export function llmTokenLimitToSend(
  state: LlmState,
  flags: { byokEnabled: boolean; configLoading: boolean },
): number | undefined {
  if (!llmChoiceToSend(state, flags)) return undefined
  const window = state.connections[state.active]?.contextWindow
  return typeof window === 'number' && Number.isInteger(window) && window > 0 ? window : undefined
}
