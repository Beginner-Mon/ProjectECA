/**
 * The LLM context object and its accessor.
 *
 * Split from contexts/LlmContext.tsx for the same reason useMotion and
 * useTheme are: a file that exports both a component and a hook breaks Fast
 * Refresh, so the provider lives alone in contexts/ and everything else here.
 */

import { createContext, useContext } from 'react'
import type { LlmConfig } from '@/lib/api'
import type { LlmCapability, LlmConnection, LlmState } from '@/lib/llmProvider'

export interface LlmContextType {
  /** Null when the backend has no /llm/config route (BYOK off) or it failed. */
  config: LlmConfig | null
  /** False until the config fetch settles — the providers list waits on it. */
  configLoading: boolean
  state: LlmState
  /** False when there is no signed-in account (demo mode owns no keys). */
  hasAccount: boolean
  /** False while config is null — the UI then offers ECA only. */
  byokEnabled: boolean
  /** Capabilities of whatever answers now: the active connection's, else ECA's. */
  activeCapabilities: LlmCapability[]
  setActive: (id: 'eca' | string) => void
  saveConnection: (conn: LlmConnection) => void
  removeConnection: (presetId: string) => void
}

export const LlmContext = createContext<LlmContextType | null>(null)

export function useLlm(): LlmContextType {
  const ctx = useContext(LlmContext)
  if (!ctx) throw new Error('useLlm must be used within LlmProvider')
  return ctx
}
