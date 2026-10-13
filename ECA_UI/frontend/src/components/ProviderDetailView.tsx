import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, RefreshCw, Trash2 } from 'lucide-react'
import type { LlmPreset } from '../lib/llmPresets'
import { catalogIdForBaseUrl, groupModels, loadCatalog, normalizeModelId } from '../lib/modelCatalog'
import { checkConnection, type CheckConn, type CheckError, type CheckResult } from '../lib/llmCheck'
import { useLlm } from '../hooks/useLlm'
import type { LlmCapability, LlmOmit, LlmStatus } from '../lib/llmProvider'

interface ProviderDetailViewProps {
  /** The card that was opened: a hand-written preset or one read from the catalog. */
  provider?: LlmPreset
  /** Called once the user chooses to use this key. The settings modal closes on it. */
  onUsed?: () => void
}

/** What the last check learned about the chosen model — saved only on "use". */
interface Checked {
  capabilities: LlmCapability[]
  omit: LlmOmit[]
  contextWindow?: number
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
 * NOTHING IS SAVED UNTIL "USE THIS API KEY" IS PRESSED. Typing or pasting a key
 * checks it, and the result lives in this component only: leave the screen and
 * it is gone, whether the key was accepted or rejected. A key that was tried
 * and abandoned must not linger on the provider's card.
 *
 * The model is picked from the provider's own list and never typed: a person
 * cannot be expected to know a model id, and a mistyped one fails the check.
 */
export default function ProviderDetailView({ provider, onUsed }: ProviderDetailViewProps) {
  const { t } = useTranslation()
  const { state, saveConnection, removeConnection, setActive } = useLlm()
  const presetId = provider?.id ?? ''
  const preset = provider
  const stored = presetId ? state.connections[presetId] : undefined

  const [baseUrl, setBaseUrl] = useState(() => stored?.baseUrl ?? preset?.baseUrl ?? '')
  const [apiKey, setApiKey] = useState(() => stored?.apiKey ?? '')
  const [model, setModel] = useState(() => stored?.model ?? '')
  const [models, setModels] = useState<string[]>([])
  /** True once a listing has been tried, whatever it returned. */
  const [listAttempted, setListAttempted] = useState(false)
  const [status, setStatus] = useState<LlmStatus>(() => stored?.status ?? 'unconfigured')
  const [checked, setChecked] = useState<Checked | null>(() =>
    stored
      ? { capabilities: stored.capabilities, omit: stored.omit, contextWindow: stored.contextWindow }
      : null,
  )
  /** With status 'unverified': the provider checks keys and took this one. */
  const [keyAccepted, setKeyAccepted] = useState(() => stored?.keyAccepted === true)
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

  // Catalog loads once per session (cached in the module, normally already by
  // the provider list) — never on the chat path.
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

  const applyOk = useCallback((seq: number, modelUsed: string, result: Checked & { models: string[] }) => {
    if (seqRef.current !== seq) return
    lastCheckedModel.current = modelUsed
    setModels(result.models)
    setListAttempted(true)
    setChecked({ capabilities: result.capabilities, omit: result.omit, contextWindow: result.contextWindow })
    setError(null)
    setStatus('configured')
    setChecking(false)
  }, [])

  const applyFail = useCallback((seq: number, result: CheckResult, modelUsed: string) => {
    if (seqRef.current !== seq) return
    const err = result.error ?? { kind: 'network' as const }
    setModels(result.models)
    setListAttempted(true)
    setChecking(false)
    if (result.untested && modelUsed) {
      // The models were listed; it is the trial message this provider refuses
      // from a browser. That is not a verdict on the key — the server makes
      // the real calls and is not subject to it. Usable, marked unverified,
      // and the first chat turn is the test of the model.
      lastCheckedModel.current = modelUsed
      setChecked({ capabilities: result.capabilities, omit: result.omit, contextWindow: result.contextWindow })
      setKeyAccepted(result.untested.keyAccepted)
      setError(null)
      setStatus('unverified')
      return
    }
    setChecked(null)
    setKeyAccepted(false)
    setError(err)
    setStatus(err.kind === 'network' ? 'unconfigured' : 'invalid')
  }, [])

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
        applyFail(seq, listed, '')
        return
      }
      const suggested = listed.suggested ?? ''
      if (!suggested) {
        // The key was not refused, but there is no model to pick from.
        setModels(listed.models)
        setListAttempted(true)
        setChecked(null)
        setChecking(false)
        setStatus('unconfigured')
        return
      }
      setModel(suggested)
      const result = await checkConnection(buildConn(base, key), suggested, catalog)
      if (seqRef.current !== seq) return
      if (result.ok) applyOk(seq, suggested, result)
      else applyFail(seq, result, suggested)
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
      const result = await checkConnection(buildConn(base, key), modelId.trim(), catalog)
      if (seqRef.current !== seq) return
      if (result.ok) applyOk(seq, modelId.trim(), result)
      else applyFail(seq, result, modelId.trim())
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
        setChecked(null)
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

  const usable = (status === 'configured' || status === 'unverified') && checked !== null && model !== ''

  /** The one place a key is written to storage. */
  const handleUse = useCallback(() => {
    if (!provider || !usable || !checked) return
    saveConnection({
      presetId,
      name: provider.name,
      baseUrl: baseUrl.trim(),
      apiKey,
      model,
      capabilities: checked.capabilities,
      omit: checked.omit,
      ...(checked.contextWindow !== undefined ? { contextWindow: checked.contextWindow } : {}),
      status: status === 'unverified' ? 'unverified' : 'configured',
      ...(status === 'unverified' && keyAccepted ? { keyAccepted: true } : {}),
    })
    setActive(presetId)
    onUsed?.()
  }, [apiKey, baseUrl, checked, keyAccepted, model, onUsed, presetId, provider, saveConnection, setActive, status, usable])

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
    setChecked(null)
    setChecking(false)
    setStatus('unconfigured')
    setError(null)
    setBaseUrl(preset?.baseUrl ?? '')
  }, [preset, presetId, removeConnection])

  const groups = useMemo(
    () => groupModels(catalog, models, preset?.catalogId ?? catalogIdForBaseUrl(catalog, baseUrl)),
    [baseUrl, catalog, models, preset],
  )

  if (!provider) return null

  const busy = status === 'validating' || checking
  // "In use" means: what is on screen is exactly what is saved and selected.
  // Change the model or the key and it becomes a draft again, with the button
  // back, until "use" is pressed — nothing on this screen applies by itself.
  const matchesSaved =
    stored !== undefined &&
    stored.apiKey === apiKey &&
    stored.baseUrl === baseUrl.trim() &&
    stored.model === model
  const inUse =
    matchesSaved &&
    state.active === presetId &&
    (stored.status === 'configured' || stored.status === 'unverified')

  const statusText = error
    ? t(errorKey(error.kind))
    : busy
      ? t('providers.checking')
      : status === 'configured'
        ? t('providers.connected')
        : status === 'unverified'
          ? t(keyAccepted ? 'providers.key_accepted_hint' : 'providers.unverified_hint')
          : status === 'invalid'
            ? t('providers.invalid')
            : t('providers.not_connected')
  const dotClass = error || status === 'invalid'
    ? 'bg-red-500'
    : busy
      ? 'bg-yellow-500 animate-pulse'
      : status === 'configured' || (status === 'unverified' && keyAccepted)
        ? 'bg-green-500'
        : status === 'unverified'
          ? 'bg-blue-500'
          : 'bg-muted-foreground/40'

  const modelPlaceholder = busy
    ? t('providers.model_loading')
    : listAttempted && models.length === 0
      ? t('providers.model_unlisted')
      : t('providers.model_need_key')
  // The saved model may not be in the list (or the list may not have loaded
  // yet); it still has to be shown as the current choice.
  const modelMissingFromList =
    model !== '' && !groups.recommended.includes(model) && !groups.other.includes(model)
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
        <div className="flex items-start gap-2 min-h-5">
          <span className={`w-2.5 h-2.5 mt-[5px] rounded-full shrink-0 ${dotClass}`} />
          <span className={`text-sm ${error ? 'text-destructive' : 'text-muted-foreground'}`}>{statusText}</span>
        </div>
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
        {groups.unsupported.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {t('providers.model_unsupported_note', { n: groups.unsupported.length })}
          </p>
        )}
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
          <p className="text-xs text-muted-foreground">
            {t('providers.in_use_caption', { model: normalizeModelId(model) })}
          </p>
        </div>
      ) : (
        <button
          type="button"
          onClick={handleUse}
          disabled={!usable || busy}
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
        )}
      </div>
    </div>
  )
}
