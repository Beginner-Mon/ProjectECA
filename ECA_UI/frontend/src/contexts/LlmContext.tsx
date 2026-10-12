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
 *
 * Updaters are functional (setState(prev => …)) so a late event — a
 * provider_notice arriving at the end of a 30s stream — cannot overwrite a
 * newer state with the one its closure was born with. The localStorage write
 * inside is idempotent, so StrictMode's double-invoked updater is harmless.
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

  const setActive = useCallback(
    (id: 'eca' | string) => {
      setState((prev) => {
        if (prev.active === id) return prev
        const next = { ...prev, active: id }
        saveLlmState(sub, next)
        return next
      })
    },
    [sub],
  )

  const saveConnection = useCallback(
    (conn: LlmConnection) => {
      setState((prev) => {
        const next = {
          ...prev,
          connections: { ...prev.connections, [conn.presetId]: conn },
        }
        saveLlmState(sub, next)
        return next
      })
    },
    [sub],
  )

  const removeConnection = useCallback(
    (presetId: string) => {
      setState((prev) => {
        if (!(presetId in prev.connections) && prev.active !== presetId) return prev
        const next = { ...prev, connections: { ...prev.connections } }
        delete next.connections[presetId]
        if (next.active === presetId) next.active = 'eca'
        saveLlmState(sub, next)
        return next
      })
    },
    [sub],
  )

  const markInvalid = useCallback(
    (presetId: string) => {
      setState((prev) => {
        const conn = prev.connections[presetId]
        if (!conn || conn.status === 'invalid') return prev
        const next = {
          ...prev,
          connections: { ...prev.connections, [presetId]: { ...conn, status: 'invalid' as const } },
        }
        saveLlmState(sub, next)
        return next
      })
    },
    [sub],
  )

  const value = useMemo(() => {
    const choice = activeLlmChoice(state)
    const activeCapabilities: LlmCapability[] =
      choice !== null ? choice.capabilities : (config?.eca.capabilities ?? FALLBACK_ECA_CAPABILITIES)
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
      markInvalid,
    }
  }, [config, configLoading, state, sub, setActive, saveConnection, removeConnection, markInvalid])

  return <LlmContext.Provider value={value}>{children}</LlmContext.Provider>
}
