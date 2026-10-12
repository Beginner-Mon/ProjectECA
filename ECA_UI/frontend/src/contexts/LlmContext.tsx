import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { fetchLlmConfig, type LlmConfig } from '@/lib/api'
import {
  activeLlmChoice,
  defaultLlmState,
  loadLlmState,
  saveLlmState,
  type LlmCapability,
  type LlmConnection,
  type LlmState,
} from '@/lib/llmProvider'
import { cognitoSub } from '@/lib/api'
import { LlmContext } from '@/hooks/useLlm'

const FALLBACK_ECA_CAPABILITIES: LlmCapability[] = ['text', 'answer', 'tools']

/**
 * One fetch of GET /llm/config per page load, plus this browser's BYOK state.
 *
 * `config === null` means BYOK is off (an older backend with no route, or a
 * failed fetch): the UI offers ECA only and streamChat sends no `llm`.
 * The key itself never leaves localStorage except inside a POST /chat body.
 */
export function LlmProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<LlmConfig | null>(null)
  const [configLoading, setConfigLoading] = useState(true)
  const [sub, setSub] = useState<string | null>(null)
  const [state, setState] = useState<LlmState>(() => defaultLlmState())

  useEffect(() => {
    let cancelled = false
    void (async () => {
      const currentSub = await cognitoSub()
      if (cancelled) return
      setSub(currentSub)
      setState(loadLlmState(currentSub))
      try {
        const cfg = await fetchLlmConfig()
        if (!cancelled) setConfig(cfg)
      } catch {
        if (!cancelled) setConfig(null)
      } finally {
        if (!cancelled) setConfigLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const persist = useCallback(
    (next: LlmState) => {
      setState(next)
      saveLlmState(sub, next)
    },
    [sub],
  )

  const setActive = useCallback(
    (id: 'eca' | string) => {
      persist({ ...state, active: id })
    },
    [persist, state],
  )

  const saveConnection = useCallback(
    (conn: LlmConnection) => {
      persist({
        ...state,
        connections: { ...state.connections, [conn.presetId]: conn },
      })
    },
    [persist, state],
  )

  const removeConnection = useCallback(
    (presetId: string) => {
      const next = { ...state, connections: { ...state.connections } }
      delete next.connections[presetId]
      if (next.active === presetId) next.active = 'eca'
      persist(next)
    },
    [persist, state],
  )

  const value = useMemo(() => {
    const choice = activeLlmChoice(state)
    const activeCapabilities: LlmCapability[] =
      choice !== null
        ? choice.capabilities
        : (config?.eca.capabilities ?? FALLBACK_ECA_CAPABILITIES)
    return {
      config,
      configLoading,
      state,
      hasAccount: sub !== null,
      byokEnabled: config?.byok === true,
      activeCapabilities,
      setActive,
      saveConnection,
      removeConnection,
    }
  }, [config, configLoading, state, sub, setActive, saveConnection, removeConnection])

  return <LlmContext.Provider value={value}>{children}</LlmContext.Provider>
}
