import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, RefreshCw, Trash2 } from 'lucide-react'
import { presetById } from '../lib/llmPresets'
import { groupModels, loadCatalog, normalizeModelId } from '../lib/modelCatalog'
import { checkConnection, type CheckConn, type CheckError } from '../lib/llmCheck'
import { useLlm } from '../hooks/useLlm'
import type { LlmCapability, LlmStatus } from '../lib/llmProvider'

interface ProviderDetailViewProps {
  provider?: { id: string; name: string }
  /** Called once the user chooses to use this key. The settings modal closes on it. */
  onUsed?: () => void
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

const FIELD =
  'px-4 py-3 rounded-xl border border-border/40 bg-card text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-foreground transition-colors'
const ICON_BUTTON =
  'shrink-0 w-12 grid place-items-center rounded-xl border border-border/40 bg-card text-muted-foreground transition-colors disabled:opacity-40 disabled:cursor-default'

/**
 * One provider's API key screen, top to bottom: the key (with re-check and
 * delete beside it), one status line, the model picker, the "use" action, and
 * the endpoint tucked into Advanced settings.
 *
 * The model is picked from the provider's own list, never typed: a person
 * cannot be expected to know a model id, and a mistyped one fails the check.
 * The single exception is a provider that publishes no list at all — then, and
 * only then, a model-name field appears under Advanced settings.
 */
export default function ProviderDetailView({ provider, onUsed }: ProviderDetailViewProps) {
  const { t } = useTranslation()
  const { state, saveConnection, removeConnection, setActive } = useLlm()
  const presetId = provider?.id ?? ''
  const preset = presetId ? presetById(presetId) : undefined
  const stored = presetId ? state.connections[presetId] : undefined

  const [baseUrl, setBaseUrl] = useState(() => stored?.baseUrl ?? preset?.baseUrl ?? '')
  const [apiKey, setApiKey] = useState(() => stored?.apiKey ?? '')
  const [model, setModel] = useState(() => stored?.model ?? '')
  const [models, setModels] = useState<string[]>([])
  /** True once a listing has been tried, whatever it returned. */
  const [listAttempted, setListAttempted] = useState(false)
  const [status, setStatus] = useState<LlmStatus>(() => stored?.status ?? 'unconfigured')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState<CheckError | null>(null)
  const [catalog, setCatalog] = useState<unknown | null>(null)
  // Open from the start only when there is no address to fall back on (the
  // "OpenAI Compatible" entry): there the endpoint is the first thing needed.
  const [advancedOpen, setAdvancedOpen] = useState(() => !(stored?.baseUrl ?? preset?.baseUrl))

  const seqRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastCheckedModel = useRef(stored?.model ?? '')
  const initial = useRef({ baseUrl, apiKey })

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

  // A saved key opens knowing only the model it was saved with. List once so
  // the picker has something to pick from: a listing costs the key nothing and
  // changes no status. Skipped if a check started in the meantime (seq moved).
  useEffect(() => {
    const { baseUrl: base, apiKey: key } = initial.current
    if (!base.trim() || !key) return
    let cancelled = false
    const seq = seqRef.current
    void checkConnection(buildConn(base, key)).then((listed) => {
      if (cancelled || seqRef.current !== seq || !listed.ok) return
      setModels(listed.models)
      setListAttempted(true)
    })
    return () => {
      cancelled = true
    }
  }, [buildConn])

  // Reset between providers is handled by the parent's key={provider.id}:
  // a different provider remounts this view, so the useState initialisers
  // above are the reset — no synchronising effect needed.

  const applyOk = useCallback(
    (
      seq: number,
      base: string,
      key: string,
      modelUsed: string,
      result: { models: string[]; capabilities: LlmCapability[]; omit: Parameters<typeof saveConnection>[0]['omit'] },
    ) => {
      if (seqRef.current !== seq) return
      lastCheckedModel.current = modelUsed
      setModels(result.models)
      setListAttempted(true)
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
    (seq: number, err: CheckError, base: string, key: string, modelUsed: string, listed: string[]) => {
      if (seqRef.current !== seq) return
      setModels(listed)
      setListAttempted(true)
      setError(err)
      setChecking(false)
      if (err.kind === 'network') {
        // The provider may simply refuse browser calls. Stay saveable through
        // the explicit "save anyway" button, which needs a typed model name —
        // so show where to type it. Nothing is persisted yet.
        setStatus((prev) => (prev === 'configured' ? prev : 'unconfigured'))
        setAdvancedOpen(true)
        return
      }
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
        applyFail(seq, listed.error ?? { kind: 'network' }, base, key, '', [])
        return
      }
      const suggested = listed.suggested ?? ''
      if (!suggested) {
        // The key works but the provider publishes no model list: the one case
        // where a model name has to be typed. Open the place to type it.
        setModels(listed.models)
        setListAttempted(true)
        setChecking(false)
        setStatus('unconfigured')
        setAdvancedOpen(true)
        return
      }
      setModel(suggested)
      const checked = await checkConnection(buildConn(base, key), suggested, catalog)
      if (seqRef.current !== seq) return
      if (checked.ok) {
        applyOk(seq, base, key, suggested, checked)
      } else {
        applyFail(seq, checked.error ?? { kind: 'network' }, base, key, suggested, checked.models)
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
        applyOk(seq, base, key, modelId.trim(), checked)
      } else {
        applyFail(seq, checked.error ?? { kind: 'network' }, base, key, modelId.trim(), checked.models)
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

  /** Picked from the list: a complete choice, so check it straight away. */
  const onModelPick = useCallback(
    (value: string) => {
      setModel(value)
      if (timerRef.current) clearTimeout(timerRef.current)
      if (value === lastCheckedModel.current) return
      void runModelCheck(baseUrl, apiKey, value)
    },
    [apiKey, baseUrl, runModelCheck],
  )

  /** Typed (providers with no list only): wait for the typing to stop. */
  const onModelType = useCallback(
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

  const handleRecheck = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    void (model.trim() ? runModelCheck(baseUrl, apiKey, model) : runAuto(baseUrl, apiKey))
  }, [apiKey, baseUrl, model, runAuto, runModelCheck])

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
    setStatus('unverified')
    setError(null)
  }, [apiKey, baseUrl, model, presetId, provider, saveConnection])

  const handleUse = useCallback(() => {
    setActive(presetId)
    onUsed?.()
  }, [onUsed, presetId, setActive])

  const handleStop = useCallback(() => {
    setActive('eca')
  }, [setActive])

  const handleDelete = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    seqRef.current++
    // Also hands the turn back to ECA when this was the key in use.
    removeConnection(presetId)
    setApiKey('')
    setModel('')
    lastCheckedModel.current = ''
    setModels([])
    setListAttempted(false)
    setChecking(false)
    setStatus('unconfigured')
    setError(null)
    setBaseUrl(preset?.baseUrl ?? '')
  }, [preset, presetId, removeConnection])

  const groups = useMemo(
    () => groupModels(catalog, models, preset?.catalogId),
    [catalog, models, preset],
  )

  if (!provider) return null

  const canUse = status === 'configured' || status === 'unverified'
  // Read from the SAVED connection, not the local status: re-checking a model
  // while the key is in use must not make the "in use" badge blink away.
  const inUse =
    state.active === presetId && (stored?.status === 'configured' || stored?.status === 'unverified')
  const busy = status === 'validating' || checking
  // A provider that answered but published no list: the model has to be typed.
  const needsTypedModel = listAttempted && models.length === 0

  const statusText = error
    ? t(errorKey(error.kind))
    : busy
      ? t('providers.checking')
      : status === 'configured'
        ? t('providers.connected')
        : status === 'unverified'
          ? t('providers.unverified')
          : status === 'invalid'
            ? t('providers.invalid')
            : t('providers.not_connected')
  const dotClass = error || status === 'invalid'
    ? 'bg-red-500'
    : busy
      ? 'bg-yellow-500 animate-pulse'
      : status === 'configured'
        ? 'bg-green-500'
        : status === 'unverified'
          ? 'bg-blue-500'
          : 'bg-muted-foreground/40'

  const modelPlaceholder = busy
    ? t('providers.model_loading')
    : needsTypedModel
      ? t('providers.model_unlisted')
      : t('providers.model_need_key')
  // The saved model may not be in the list (or the list may not have loaded
  // yet); it still has to be shown as the current choice.
  const modelMissingFromList = model !== '' && !models.includes(model)
  const grouped = groups.recommended.length > 0 && groups.other.length > 0

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="space-y-2">
        <label htmlFor={`llm-key-${presetId}`} className="block text-sm font-medium text-foreground">
          {t('providers.api_key_label')}
        </label>
        <div className="flex gap-2">
          <input
            id={`llm-key-${presetId}`}
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            onPaste={onPasteKey}
            placeholder={t('providers.placeholder_api_key')}
            className={`flex-1 min-w-0 ${FIELD}`}
          />
          <button
            type="button"
            onClick={handleRecheck}
            disabled={busy || !apiKey}
            title={t('providers.recheck')}
            aria-label={t('providers.recheck')}
            className={`${ICON_BUTTON} enabled:hover:text-foreground enabled:hover:border-foreground`}
          >
            <RefreshCw className={`w-[18px] h-[18px] ${busy ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={handleDelete}
            disabled={!apiKey && !stored}
            title={t('providers.delete_key')}
            aria-label={t('providers.delete_key')}
            className={`${ICON_BUTTON} enabled:hover:text-destructive enabled:hover:border-destructive`}
          >
            <Trash2 className="w-[18px] h-[18px]" />
          </button>
        </div>
        <div className="flex items-center gap-2 min-h-5">
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${dotClass}`} />
          <span className={`text-sm ${error ? 'text-destructive' : 'text-muted-foreground'}`}>{statusText}</span>
        </div>
        {error?.kind === 'network' && (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground">{t('providers.network_hint')}</p>
            <button
              type="button"
              onClick={handleStillSave}
              disabled={!model.trim()}
              className="px-4 py-2 rounded-lg text-sm font-medium border border-border/40 hover:border-foreground transition-colors disabled:opacity-50"
            >
              {t('providers.save_anyway')}
            </button>
          </div>
        )}
        {preset?.note && <p className="text-xs text-muted-foreground">{t(`providers.${preset.note}`)}</p>}
      </div>

      <div className="space-y-2">
        <label htmlFor={`llm-model-${presetId}`} className="block text-sm font-medium text-foreground">
          {t('providers.model_label')}
        </label>
        <div className="relative">
          <select
            id={`llm-model-${presetId}`}
            value={model}
            onChange={(e) => onModelPick(e.target.value)}
            disabled={models.length === 0}
            className={`w-full appearance-none pr-11 ${FIELD} enabled:cursor-pointer disabled:text-muted-foreground`}
          >
            {model === '' && <option value="">{modelPlaceholder}</option>}
            {modelMissingFromList && <option value={model}>{normalizeModelId(model)}</option>}
            {grouped ? (
              <>
                <optgroup label={t('providers.model_group_recommended')}>
                  {groups.recommended.map((id) => (
                    <option key={id} value={id}>
                      {normalizeModelId(id)}
                    </option>
                  ))}
                </optgroup>
                <optgroup label={t('providers.model_group_other')}>
                  {groups.other.map((id) => (
                    <option key={id} value={id}>
                      {normalizeModelId(id)}
                    </option>
                  ))}
                </optgroup>
              </>
            ) : (
              [...groups.recommended, ...groups.other].map((id) => (
                <option key={id} value={id}>
                  {normalizeModelId(id)}
                </option>
              ))
            )}
          </select>
          <ChevronDown className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
        </div>
      </div>

      {inUse ? (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <span className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold bg-green-500/15 border border-green-500/40 text-green-700 dark:text-green-400">
              <Check className="w-4 h-4" />
              {t('providers.active')}
            </span>
            <button
              type="button"
              onClick={handleStop}
              className="text-sm text-muted-foreground underline underline-offset-4 hover:text-foreground transition-colors"
            >
              {t('providers.stop_using')}
            </button>
          </div>
          {stored?.model && (
            <p className="text-xs text-muted-foreground">
              {t('providers.in_use_caption', { model: normalizeModelId(stored.model) })}
            </p>
          )}
        </div>
      ) : (
        <button
          type="button"
          onClick={handleUse}
          disabled={!canUse}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {t('providers.use_api_key')}
        </button>
      )}

      <div className="border-t border-border/40 pt-4 space-y-4">
        <button
          type="button"
          onClick={() => setAdvancedOpen((open) => !open)}
          aria-expanded={advancedOpen}
          className="flex w-full items-center justify-between gap-3 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
        >
          {t('providers.advanced')}
          <ChevronDown className={`w-4 h-4 transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
        </button>
        {advancedOpen && (
          <div className="space-y-4">
            <div className="space-y-2">
              <label htmlFor={`llm-endpoint-${presetId}`} className="block text-sm font-medium text-foreground">
                {t('providers.endpoint_url')}
              </label>
              <input
                id={`llm-endpoint-${presetId}`}
                type="text"
                value={baseUrl}
                onChange={(e) => onBaseUrlChange(e.target.value)}
                placeholder={t('providers.base_url_placeholder')}
                className={`w-full ${FIELD}`}
              />
              {preset?.baseUrl && (
                <p className="text-xs text-muted-foreground">{t('providers.endpoint_hint', { name: provider.name })}</p>
              )}
            </div>
            {needsTypedModel && (
              <div className="space-y-2">
                <label htmlFor={`llm-model-name-${presetId}`} className="block text-sm font-medium text-foreground">
                  {t('providers.model_name')}
                </label>
                <input
                  id={`llm-model-name-${presetId}`}
                  type="text"
                  value={model}
                  onChange={(e) => onModelType(e.target.value)}
                  className={`w-full ${FIELD}`}
                />
                <p className="text-xs text-muted-foreground">{t('providers.model_name_hint')}</p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
