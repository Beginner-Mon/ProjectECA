import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { presetById } from '../lib/llmPresets'
import { loadCatalog } from '../lib/modelCatalog'
import { checkConnection, type CheckConn, type CheckError } from '../lib/llmCheck'
import { useLlm } from '../hooks/useLlm'
import type { LlmCapability, LlmStatus } from '../lib/llmProvider'

interface ProviderDetailViewProps {
  provider?: { id: string; name: string }
}

function errorKey(kind: CheckError['kind']): string {
  switch (kind) {
    case 'auth':
      return 'providers.error_auth'
    case 'quota':
      return 'providers.error_quota'
    case 'model_not_found':
      return 'providers.error_model_not_found'
    case 'bad_request':
      return 'providers.error_bad_request'
    case 'server':
      return 'providers.error_server'
    case 'network':
      return 'providers.error_network'
    case 'no_text':
      return 'providers.error_no_text'
  }
}

export default function ProviderDetailView({ provider }: ProviderDetailViewProps) {
  const { t } = useTranslation()
  const { state, saveConnection, removeConnection, setActive } = useLlm()
  const presetId = provider?.id ?? ''
  const preset = presetId ? presetById(presetId) : undefined
  const stored = presetId ? state.connections[presetId] : undefined

  const [baseUrl, setBaseUrl] = useState(() => stored?.baseUrl ?? preset?.baseUrl ?? '')
  const [apiKey, setApiKey] = useState(() => stored?.apiKey ?? '')
  const [model, setModel] = useState(() => stored?.model ?? '')
  const [models, setModels] = useState<string[]>([])
  const [modelsListed, setModelsListed] = useState(false)
  const [capabilities, setCapabilities] = useState<LlmCapability[]>(() => stored?.capabilities ?? [])
  const [status, setStatus] = useState<LlmStatus>(() => stored?.status ?? 'unconfigured')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<CheckError | null>(null)
  const [catalog, setCatalog] = useState<unknown | null>(null)

  const seqRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastCheckedModel = useRef(stored?.model ?? '')

  const buildConn = useCallback(
    (base: string, key: string): CheckConn => ({
      baseUrl: base.trim(),
      apiKey: key,
      checkHeaders: preset?.checkHeaders,
      catalogId: preset?.catalogId,
    }),
    [preset],
  )

  // Catalog loads once per session (cached in the module); the detail view is
  // what triggers it — never the chat path.
  useEffect(() => {
    let cancelled = false
    void loadCatalog().then((cat) => {
      if (!cancelled) setCatalog(cat)
    })
    return () => {
      cancelled = true
    }
  }, [])

  // Reset between providers is handled by the parent's key={provider.id}:
  // a different provider remounts this view, so the useState initialisers
  // above are the reset — no synchronising effect needed.

  const applyOk = useCallback(
    (
      seq: number,
      base: string,
      key: string,
      modelUsed: string,
      result: { models: string[]; modelsListed: boolean; capabilities: LlmCapability[]; omit: Parameters<typeof saveConnection>[0]['omit'] },
    ) => {
      if (seqRef.current !== seq) return
      lastCheckedModel.current = modelUsed
      setModels(result.models)
      setModelsListed(result.modelsListed)
      setCapabilities(result.capabilities)
      setError(null)
      setStatus('configured')
      setChecking(false)
      if (provider) {
        saveConnection({
          presetId,
          name: provider.name,
          baseUrl: base.trim(),
          apiKey: key,
          model: modelUsed,
          capabilities: result.capabilities,
          omit: result.omit,
          status: 'configured',
        })
      }
    },
    [presetId, provider, saveConnection],
  )

  const applyFail = useCallback(
    (
      seq: number,
      err: CheckError,
      base: string,
      key: string,
      modelUsed: string,
      listed: { models: string[]; modelsListed: boolean },
    ) => {
      if (seqRef.current !== seq) return
      setModels(listed.models)
      setModelsListed(listed.modelsListed)
      setError(err)
      setChecking(false)
      if (err.kind === 'network') {
        // Stay saveable via the explicit still-save button — do not persist yet.
        setStatus((prev) => (prev === 'configured' ? prev : 'unconfigured'))
        return
      }
      setCapabilities([])
      setStatus('invalid')
      if (provider) {
        saveConnection({
          presetId,
          name: provider.name,
          baseUrl: base.trim(),
          apiKey: key,
          model: modelUsed.trim(),
          capabilities: [],
          omit: [],
          status: 'invalid',
        })
      }
    },
    [presetId, provider, saveConnection],
  )

  /** Full auto flow: list → suggest → check the suggestion. */
  const runAuto = useCallback(
    async (base: string, key: string) => {
      if (!base.trim() || !key) return
      const seq = ++seqRef.current
      setChecking(true)
      setStatus('validating')
      setError(null)
      const listed = await checkConnection(buildConn(base, key), undefined, catalog)
      if (seqRef.current !== seq) return
      if (!listed.ok) {
        applyFail(seq, listed.error ?? { kind: 'network' }, base, key, '', {
          models: [],
          modelsListed: false,
        })
        return
      }
      const suggested = listed.suggested ?? ''
      if (!suggested) {
        // Listed but nothing to suggest (empty list): wait for a manual model.
        if (seqRef.current !== seq) return
        setModels(listed.models)
        setModelsListed(listed.modelsListed)
        setChecking(false)
        setStatus('unconfigured')
        return
      }
      setModel(suggested)
      const checked = await checkConnection(buildConn(base, key), suggested, catalog)
      if (seqRef.current !== seq) return
      if (checked.ok) {
        applyOk(seq, base, key, suggested, {
          models: checked.models,
          modelsListed: checked.modelsListed,
          capabilities: checked.capabilities,
          omit: checked.omit,
        })
      } else {
        applyFail(seq, checked.error ?? { kind: 'network' }, base, key, suggested, {
          models: checked.models,
          modelsListed: checked.modelsListed,
        })
      }
    },
    [applyFail, applyOk, buildConn, catalog],
  )

  const runModelCheck = useCallback(
    async (base: string, key: string, modelId: string) => {
      if (!base.trim() || !key || !modelId.trim()) return
      const seq = ++seqRef.current
      setChecking(true)
      setStatus('validating')
      setError(null)
      const checked = await checkConnection(buildConn(base, key), modelId.trim(), catalog)
      if (seqRef.current !== seq) return
      if (checked.ok) {
        applyOk(seq, base, key, modelId.trim(), {
          models: checked.models,
          modelsListed: checked.modelsListed,
          capabilities: checked.capabilities,
          omit: checked.omit,
        })
      } else {
        applyFail(seq, checked.error ?? { kind: 'network' }, base, key, modelId.trim(), {
          models: checked.models,
          modelsListed: checked.modelsListed,
        })
      }
    },
    [applyFail, applyOk, buildConn, catalog],
  )

  const schedule = useCallback((fn: () => void) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(fn, 600)
  }, [])

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    [],
  )

  const onBaseUrlChange = useCallback(
    (value: string) => {
      setBaseUrl(value)
      schedule(() => void runAuto(value, apiKey))
    },
    [apiKey, runAuto, schedule],
  )

  const onApiKeyChange = useCallback(
    (value: string) => {
      setApiKey(value)
      if (!value) {
        if (timerRef.current) clearTimeout(timerRef.current)
        seqRef.current++
        setChecking(false)
        setError(null)
        setStatus('unconfigured')
        return
      }
      schedule(() => void runAuto(baseUrl, value))
    },
    [baseUrl, runAuto, schedule],
  )

  const onModelChange = useCallback(
    (value: string) => {
      setModel(value)
      if (value === lastCheckedModel.current) return
      schedule(() => void runModelCheck(baseUrl, apiKey, value))
    },
    [apiKey, baseUrl, runModelCheck, schedule],
  )

  const onPasteKey = useCallback(
    (e: React.ClipboardEvent<HTMLInputElement>) => {
      if (timerRef.current) clearTimeout(timerRef.current)
      // Paste lands after this event — read the input's live value on next tick.
      const target = e.currentTarget
      setTimeout(() => void runAuto(baseUrl, target.value), 0)
    },
    [baseUrl, runAuto],
  )

  const handleStillSave = useCallback(() => {
    if (!provider || !model.trim()) return
    saveConnection({
      presetId,
      name: provider.name,
      baseUrl: baseUrl.trim(),
      apiKey,
      model: model.trim(),
      capabilities: ['text', 'answer', 'tools'],
      omit: [],
      status: 'unverified',
    })
    setCapabilities(['text', 'answer', 'tools'])
    setStatus('unverified')
    setError(null)
  }, [apiKey, baseUrl, model, presetId, provider, saveConnection])

  const handleUse = useCallback(() => {
    setActive(presetId)
  }, [presetId, setActive])

  const handleDelete = useCallback(() => {
    removeConnection(presetId)
    setApiKey('')
    setModel('')
    lastCheckedModel.current = ''
    setModels([])
    setModelsListed(false)
    setCapabilities([])
    setStatus('unconfigured')
    setError(null)
    setBaseUrl(preset?.baseUrl ?? '')
  }, [preset, presetId, removeConnection])

  if (!provider) return null

  const canUse = status === 'configured' || status === 'unverified'
  const statusText =
    status === 'configured'
      ? t('providers.connected')
      : status === 'unverified'
        ? t('providers.unverified')
        : status === 'invalid'
          ? t('providers.invalid')
          : status === 'validating' || checking
            ? t('providers.checking')
            : t('providers.not_connected')

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-foreground mb-1">{t('providers.insert_api_key')}</h1>
        <p className="text-sm text-muted-foreground">{t('providers.api_key_hint', { name: provider.name })}</p>
      </div>

      <div className="space-y-3">
        <label className="block text-sm font-medium text-foreground">{t('providers.base_url')}</label>
        <input
          type="text"
          value={baseUrl}
          onChange={(e) => onBaseUrlChange(e.target.value)}
          placeholder={t('providers.base_url_placeholder')}
          className="w-full px-4 py-3 rounded-xl border border-border/40 bg-card text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-foreground transition-colors"
        />
        <input
          type="password"
          value={apiKey}
          onChange={(e) => onApiKeyChange(e.target.value)}
          onPaste={onPasteKey}
          placeholder={t('providers.placeholder_api_key')}
          className="w-full px-4 py-3 rounded-xl border border-border/40 bg-card text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-foreground transition-colors"
        />
        {preset?.note && <p className="text-xs text-muted-foreground">{t(`providers.${preset.note}`)}</p>}
      </div>

      {(models.length > 0 || model || checking) && (
        <div className="space-y-3">
          <label className="block text-sm font-medium text-foreground">{t('providers.model_label')}</label>
          <input
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            placeholder={t('providers.model_placeholder')}
            list={`models-${presetId}`}
            className="w-full px-4 py-3 rounded-xl border border-border/40 bg-card text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-foreground transition-colors"
          />
          <datalist id={`models-${presetId}`}>
            {models.map((id) => (
              <option key={id} value={id} />
            ))}
          </datalist>
          {!modelsListed && <p className="text-xs text-muted-foreground">{t('providers.model_manual_hint')}</p>}
          {capabilities.length > 0 && (
            <div className="flex gap-2 flex-wrap">
              {capabilities.map((cap) => (
                <span
                  key={cap}
                  className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-secondary text-secondary-foreground"
                >
                  {cap}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="border-t border-border/40 pt-6">
        <h2 className="text-base font-semibold text-foreground mb-3">{t('providers.status')}</h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                status === 'configured'
                  ? 'bg-green-500'
                  : status === 'invalid'
                    ? 'bg-red-500'
                    : status === 'validating' || checking
                      ? 'bg-yellow-500 animate-pulse'
                      : status === 'unverified'
                        ? 'bg-blue-500'
                        : 'bg-muted-foreground/40'
              }`}
            />
            <span className="text-sm text-muted-foreground">{statusText}</span>
          </div>
          <button
            onClick={() => void (model.trim() ? runModelCheck(baseUrl, apiKey, model) : runAuto(baseUrl, apiKey))}
            disabled={checking}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {t('providers.recheck')}
          </button>
        </div>
        {error && (
          <div className="mt-3 space-y-2">
            <p className="text-sm text-destructive">{t(errorKey(error.kind))}</p>
            {error.kind === 'network' && (
              <>
                <p className="text-xs text-muted-foreground">{t('providers.network_hint')}</p>
                <button
                  onClick={handleStillSave}
                  disabled={!model.trim()}
                  className="px-4 py-2 rounded-lg text-sm font-medium border border-border/40 hover:border-foreground transition-colors disabled:opacity-50"
                >
                  {t('providers.save_anyway')}
                </button>
              </>
            )}
          </div>
        )}
        <div className="mt-4 flex gap-2">
          <button
            onClick={handleUse}
            disabled={!canUse}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {t('providers.use_connection')}
          </button>
          <button
            onClick={handleDelete}
            className="px-4 py-2 rounded-lg text-sm font-medium border border-border/40 hover:border-foreground transition-colors"
          >
            {t('common.delete')}
          </button>
        </div>
      </div>

      <p className="text-xs text-muted-foreground border-t border-border/40 pt-4">{t('providers.notice_storage')}</p>
    </div>
  )
}
