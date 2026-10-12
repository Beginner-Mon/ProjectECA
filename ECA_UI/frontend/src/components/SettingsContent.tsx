import { KeyRound, Bot, Sparkles, Brain, Cpu, Zap, Wind, Server, MonitorCog, Info, ScrollText, Languages, Package, Check } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ProviderDetailView from './ProviderDetailView'
import { useLocale } from '@/hooks/useLocale'
import { LOCALES, LOCALE_LABELS } from '@/i18n/locale'
import { LLM_PRESETS, type LlmPreset } from '@/lib/llmPresets'
import { useLlm } from '@/hooks/useLlm'
import type { ComponentType } from 'react'

interface SettingsContentProps {
  view?: 'main' | 'providers' | 'provider-detail' | 'language'
  onNavigateToProviders?: () => void
  onNavigateToGraphics?: () => void
  onNavigateToAbout?: () => void
  onNavigateToLanguage?: () => void
  onSelectProvider?: (provider: { id: string; name: string }) => void
  selectedProvider?: { id: string; name: string }
}

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

const PRESET_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  anthropic: Bot,
  gemini: Sparkles,
  deepseek: Brain,
  openai: Cpu,
  groq: Zap,
  mistral: Wind,
}

function ProvidersView({ onSelectProvider }: { onSelectProvider?: SettingsContentProps['onSelectProvider'] }) {
  const { t } = useTranslation()
  const { config, configLoading, state, setActive, byokEnabled } = useLlm()

  if (!byokEnabled && !configLoading) {
    const cards = [{ id: 'eca', name: t('providers.eca'), official: true, active: state.active === 'eca' }]
    return (
      <div className="flex-1 overflow-y-auto p-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {cards.map((provider) => (
            <button
              key={provider.id}
              onClick={() => setActive('eca')}
              className="flex justify-between items-center w-full rounded-xl border border-border/40 bg-card hover:border-foreground transition-colors group overflow-hidden"
            >
              <div className="flex flex-col justify-center py-5 px-6">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-foreground">{provider.name}</span>
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-primary bg-primary/10 px-2 py-0.5 rounded-full">{t('settings.official')}</span>
                </div>
                {provider.active && (
                  <span className="text-xs text-primary mt-1 flex items-center gap-1">
                    <Check className="w-3 h-3" />{t('providers.active')}
                  </span>
                )}
              </div>
              <div className="flex items-end justify-end w-20 shrink-0 pr-1">
                <Server className="w-[72px] h-[72px] text-muted-foreground group-hover:scale-125 group-hover:text-foreground transition-all duration-300" />
              </div>
            </button>
          ))}
        </div>
      </div>
    )
  }

  const visible: LlmPreset[] = LLM_PRESETS.filter((p) => !p.local || config?.local_endpoints === true)

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <button
          key="eca"
          onClick={() => setActive('eca')}
          className="flex justify-between items-center w-full rounded-xl border border-border/40 bg-card hover:border-foreground transition-colors group overflow-hidden"
        >
          <div className="flex flex-col justify-center py-5 px-6">
            <div className="flex items-center gap-2">
              <span className="text-base font-semibold text-foreground">{t('providers.eca')}</span>
              <span className="text-[10px] font-semibold uppercase tracking-wider text-primary bg-primary/10 px-2 py-0.5 rounded-full">{t('settings.official')}</span>
            </div>
            {state.active === 'eca' && (
              <span className="text-xs text-primary mt-1 flex items-center gap-1">
                <Check className="w-3 h-3" />{t('providers.active')}
              </span>
            )}
          </div>
          <div className="flex items-end justify-end w-20 shrink-0 pr-1">
            <Server className="w-[72px] h-[72px] text-muted-foreground group-hover:scale-125 group-hover:text-foreground transition-all duration-300" />
          </div>
        </button>
        {visible.map((preset) => {
          const Icon = PRESET_ICONS[preset.id] ?? Package
          const conn = state.connections[preset.id]
          const isActive = state.active === preset.id
          return (
            <button
              key={preset.id}
              onClick={() => {
                onSelectProvider?.({ id: preset.id, name: preset.name })
              }}
              className="flex justify-between items-center w-full rounded-xl border border-border/40 bg-card hover:border-foreground transition-colors group overflow-hidden"
            >
              <div className="flex flex-col justify-center py-5 px-6">
                <div className="flex items-center gap-2">
                  <span className="text-base font-semibold text-foreground">{preset.name}</span>
                </div>
                {isActive && (
                  <span className="text-xs text-primary mt-1 flex items-center gap-1">
                    <Check className="w-3 h-3" />{t('providers.active')}
                  </span>
                )}
                {conn && conn.status !== 'unconfigured' && (
                  <span className="text-xs text-muted-foreground mt-1">
                    {conn.status === 'configured'
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
                <Icon className="w-[72px] h-[72px] text-muted-foreground group-hover:scale-125 group-hover:text-foreground transition-all duration-300" />
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function SettingsContent({ view = 'main', onNavigateToProviders, onNavigateToGraphics, onNavigateToLanguage, onSelectProvider, selectedProvider }: SettingsContentProps) {
  const { t } = useTranslation()

  if (view === 'provider-detail') {
    // Keyed by provider: switching provider remounts the detail view, which
    // is its reset (its useState initialisers read the stored connection).
    return <ProviderDetailView key={selectedProvider?.id ?? 'none'} provider={selectedProvider} />
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
    { id: 'terms', icon: ScrollText, label: t('settings.terms'), description: t('settings.terms_desc') },
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
