import { useEffect, useMemo, useState } from 'react'
import { KeyRound, MonitorCog, Info, ScrollText, Languages, Check, Brain } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import EcaLogo from './EcaLogo'
import ProviderDetailView from './ProviderDetailView'
import ProviderIcon from './ProviderIcon'
import { useLocale } from '@/hooks/useLocale'
import { LOCALES, LOCALE_LABELS } from '@/i18n/locale'
import { providerList, type LlmPreset } from '@/lib/llmPresets'
import { activeLlmChoice } from '@/lib/llmProvider'
import { loadCatalog, normalizeModelId } from '@/lib/modelCatalog'
import { useLlm } from '@/hooks/useLlm'

interface SettingsContentProps {
  view?: 'main' | 'providers' | 'provider-detail' | 'language'
  onNavigateToProviders?: () => void
  onNavigateToGraphics?: () => void
  onNavigateToAbout?: () => void
  onNavigateToLanguage?: () => void
  onNavigateToLegal?: () => void
  onNavigateToMemory?: () => void
  onSelectProvider?: (provider: LlmPreset) => void
  selectedProvider?: LlmPreset
  /** The user chose to use a provider's key — the modal closes on it. */
  onProviderUsed?: () => void
}

// A provider card: the one in use is outlined and says so.
const CARD = 'flex justify-between items-center w-full rounded-xl border bg-card hover:border-foreground transition-colors text-left group overflow-hidden'
const CARD_IDLE = 'border-border/40'
const CARD_IN_USE = 'border-green-500'
const IN_USE_BADGE = 'text-xs font-semibold text-green-700 dark:text-green-400 mt-1 flex items-center gap-1'
// The ECA mark, drawn at 1.5x the other logos' 72px box: its drawing fills only
// about half of its own frame, so at 1x it reads much smaller than the rest.
// Scaled with a transform rather than a bigger box so the card keeps its
// height: the box still measures 72px, only the drawing grows. Hover keeps the
// same 1.25x lift the other cards have (1.5 x 1.25).
const ECA_LOGO = 'w-[72px] h-[72px] scale-[1.5] text-muted-foreground group-hover:scale-[1.875] group-hover:text-foreground transition-all duration-300'

/**
 * Detail view for website language. Rendered only after the user taps the
 * Language item — matches the drill-down pattern used by Providers/Graphics/
 * About so the main Settings list stays uniform.
 *
 * Applies on click — `react-i18next` re-renders every consumer on
 * `languageChanged`, so there is no Save button.
 *
 * Each option is labelled in its OWN language rather than the active one. The
 * person most likely to need this control is the one currently stranded in a
 * language they cannot read, and "Tiếng Việt" is findable from an English page
 * in a way that "Vietnamese" is not from a Vietnamese one.
 */
function LanguageSelectionView() {
  const { t } = useTranslation()
  const { locale, setLocale } = useLocale()

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="space-y-3">
        {LOCALES.map((code) => (
          <button
            key={code}
            onClick={() => setLocale(code)}
            aria-pressed={locale === code}
            className={`flex justify-between items-center w-full rounded-xl border px-6 py-5 transition-colors text-left ${
              locale === code
                ? 'border-foreground bg-secondary/60'
                : 'border-border/40 bg-card hover:border-foreground'
            }`}
          >
            <div className="flex flex-col">
              <span className="text-base font-semibold text-foreground">{LOCALE_LABELS[code]}</span>
              <span className="text-sm text-muted-foreground mt-0.5">
                {code === 'vi' ? 'Tiếng Việt' : 'English'} · {code}
              </span>
            </div>
            <div
              className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 transition-colors ${
                locale === code ? 'border-foreground bg-foreground' : 'border-muted-foreground/30'
              }`}
            >
              {locale === code && <div className="w-2 h-2 rounded-full bg-background" />}
            </div>
          </button>
        ))}
        <p className="text-sm text-muted-foreground pt-2">{t('settings.language_hint')}</p>
      </div>
    </div>
  )
}

function ProvidersView({ onSelectProvider }: { onSelectProvider?: SettingsContentProps['onSelectProvider'] }) {
  const { t } = useTranslation()
  const { config, configLoading, state, setActive, byokEnabled } = useLlm()
  const [catalog, setCatalog] = useState<unknown | null>(null)
  const [query, setQuery] = useState('')

  // The catalog is where most of the cards come from. Loaded once per session
  // (cached in the module), and not at all while BYOK is off.
  useEffect(() => {
    if (!byokEnabled) return
    let cancelled = false
    void loadCatalog().then((cat) => {
      if (!cancelled) setCatalog(cat)
    })
    return () => {
      cancelled = true
    }
  }, [byokEnabled])

  const localAllowed = config?.local_endpoints === true
  const all = useMemo(
    () => providerList(catalog).filter((p) => !p.local || localAllowed),
    [catalog, localAllowed],
  )
  const q = query.trim().toLowerCase()
  const visible = useMemo(() => {
    const matched = q ? all.filter((p) => p.name.toLowerCase().includes(q) || p.id.includes(q)) : all
    // The entry that takes any address stays beside ECA; after it, the cards
    // the user has set up, so the key in use is never a long scroll away. The
    // sort is stable: within each group the list keeps its own order.
    const rank = (p: LlmPreset) => (p === all[0] ? 0 : state.connections[p.id] ? 1 : 2)
    return [...matched].sort((a, b) => rank(a) - rank(b))
  }, [all, q, state.connections])

  if (!byokEnabled && !configLoading) {
    // With BYOK off nothing but ECA can answer, whatever a stale state says.
    const cards = [{ id: 'eca', name: t('providers.eca'), official: true, active: true }]
    return (
      <div className="flex-1 overflow-y-auto p-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {cards.map((provider) => (
            <button
              key={provider.id}
              onClick={() => setActive('eca')}
              className={`${CARD} ${provider.active ? CARD_IN_USE : CARD_IDLE}`}
            >
              <div className="flex flex-col justify-center py-5 px-6">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-foreground">{provider.name}</span>
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-primary bg-primary/10 px-2 py-0.5 rounded-full">{t('settings.official')}</span>
                </div>
                {provider.active && (
                  <span className={IN_USE_BADGE}>
                    <Check className="w-3 h-3" />{t('providers.active')}
                  </span>
                )}
              </div>
              <div className="flex items-end justify-end w-20 shrink-0 pr-1">
                <EcaLogo className={ECA_LOGO} />
              </div>
            </button>
          ))}
        </div>
      </div>
    )
  }

  // "In use" follows what will actually answer the next message, not just the
  // selection: a selected key that has since been rejected falls back to ECA,
  // and the cards must say ECA.
  const usedPresetId = activeLlmChoice(state) !== null ? state.active : 'eca'
  const showEca = !q || t('providers.eca').toLowerCase().includes(q)

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4">
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t('providers.search_placeholder')}
        aria-label={t('providers.search_placeholder')}
        className="w-full px-4 py-3 rounded-xl border border-border/40 bg-card text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-foreground transition-colors"
      />
      {!showEca && visible.length === 0 && (
        <p className="text-sm text-muted-foreground">{t('providers.search_empty')}</p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {showEca && (
          <button
            key="eca"
            onClick={() => setActive('eca')}
            className={`${CARD} ${usedPresetId === 'eca' ? CARD_IN_USE : CARD_IDLE}`}
          >
            <div className="flex flex-col justify-center py-5 px-6">
              <div className="flex items-center gap-2">
                <span className="text-base font-semibold text-foreground">{t('providers.eca')}</span>
                <span className="text-[10px] font-semibold uppercase tracking-wider text-primary bg-primary/10 px-2 py-0.5 rounded-full">{t('settings.official')}</span>
              </div>
              {usedPresetId === 'eca' && (
                <span className={IN_USE_BADGE}>
                  <Check className="w-3 h-3" />{t('providers.active')}
                </span>
              )}
            </div>
            <div className="flex items-end justify-end w-20 shrink-0 pr-1">
              <EcaLogo className={ECA_LOGO} />
            </div>
          </button>
        )}
        {visible.map((preset) => {
          const conn = state.connections[preset.id]
          const isActive = usedPresetId === preset.id
          return (
            <button
              key={preset.id}
              onClick={() => {
                onSelectProvider?.(preset)
              }}
              className={`${CARD} ${isActive ? CARD_IN_USE : CARD_IDLE}`}
            >
              <div className="flex flex-col justify-center py-5 px-6 min-w-0">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-base font-semibold text-foreground break-words min-w-0">{preset.name}</span>
                </div>
                {isActive && (
                  <span className={IN_USE_BADGE}>
                    <Check className="w-3 h-3" />{t('providers.active')}
                  </span>
                )}
                {isActive && conn?.model && (
                  <span className="text-xs text-muted-foreground mt-1 truncate">{normalizeModelId(conn.model)}</span>
                )}
                {!isActive && conn && conn.status !== 'unconfigured' && (
                  <span className="text-xs text-muted-foreground mt-1">
                    {conn.status === 'configured' || (conn.status === 'unverified' && conn.keyAccepted)
                      ? t('providers.connected')
                      : conn.status === 'unverified'
                        ? t('providers.unverified')
                        : conn.status === 'invalid'
                          ? t('providers.invalid')
                          : t('providers.checking')}
                  </span>
                )}
              </div>
              <div className="flex items-end justify-end w-20 shrink-0 pr-1">
                <ProviderIcon
                  presetId={preset.id}
                  catalogId={preset.catalogId}
                  className="w-[72px] h-[72px] text-muted-foreground group-hover:scale-125 group-hover:text-foreground transition-all duration-300"
                />
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function SettingsContent({ view = 'main', onNavigateToProviders, onNavigateToGraphics, onNavigateToLanguage, onNavigateToLegal, onNavigateToMemory, onSelectProvider, selectedProvider, onProviderUsed }: SettingsContentProps) {
  const { t } = useTranslation()

  if (view === 'provider-detail') {
    // Keyed by provider: switching provider remounts the detail view, which
    // is its reset (its useState initialisers read the stored connection).
    return <ProviderDetailView key={selectedProvider?.id ?? 'none'} provider={selectedProvider} onUsed={onProviderUsed} />
  }

  if (view === 'language') {
    return <LanguageSelectionView />
  }

  if (view === 'providers') {
    return <ProvidersView onSelectProvider={onSelectProvider} />
  }

  // Built inside the component, not hoisted to module scope: the labels have to
  // be re-read on every render or they keep the language they were first
  // evaluated in. `i18next.t` at module scope would run once, before any
  // language change, and this menu would be the one part of the app that never
  // switched.
  //
  // Worth knowing: `i18next/no-literal-string` does NOT catch these. It reads
  // JSX, and a string sitting in a plain object is invisible to it even though
  // it is rendered two lines below. The rule is a net, not a proof.
  const items = [
    { id: 'language', icon: Languages, label: t('settings.language'), description: t('settings.language_hint'), onClick: onNavigateToLanguage },
    { id: 'providers', icon: KeyRound, label: t('settings.providers'), description: t('settings.providers_desc'), onClick: onNavigateToProviders },
    { id: 'graphics', icon: MonitorCog, label: t('settings.graphics'), description: t('settings.graphics_desc'), onClick: onNavigateToGraphics },
    { id: 'about', icon: Info, label: t('settings.about'), description: t('settings.about_desc') },
    { id: 'memory', icon: Brain, label: t('settings.memory'), description: t('settings.memory_desc'), onClick: onNavigateToMemory },
    { id: 'terms', icon: ScrollText, label: t('settings.terms'), description: t('settings.terms_desc'), onClick: onNavigateToLegal },
  ]

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="space-y-4">
        {items.map((item) => {
          const Icon = item.icon
          return (
            <button
              key={item.id}
              onClick={item.onClick}
              className="flex justify-between items-stretch w-full rounded-xl border border-border/40 bg-card hover:border-foreground transition-colors text-left group overflow-hidden"
            >
              <div className="flex flex-col justify-center py-5 px-6">
                <span className="text-base font-semibold text-foreground">{item.label}</span>
                <p className="text-sm text-muted-foreground mt-1">{item.description}</p>
              </div>
              <div className="flex items-end justify-end w-20 shrink-0 pr-1">
                <Icon className="w-[72px] h-[72px] text-muted-foreground group-hover:scale-125 group-hover:text-foreground transition-all duration-300" />
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
