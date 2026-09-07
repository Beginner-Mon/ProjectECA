import { useState } from 'react'
import { useTranslation } from 'react-i18next'

interface ProviderDetailViewProps {
  provider?: { id: string; name: string }
}

export default function ProviderDetailView({ provider }: ProviderDetailViewProps) {
  const { t } = useTranslation()
  const [apiKey, setApiKey] = useState('')
  const [status, setStatus] = useState<'idle' | 'checking' | 'connected' | 'failed'>('idle')

  const checkStatus = async () => {
    setStatus('checking')
    await new Promise((r) => setTimeout(r, 1500))
    setStatus(apiKey.length > 0 ? 'connected' : 'failed')
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold text-foreground mb-1">{t('providers.insert_api_key')}</h1>
        <p className="text-sm text-muted-foreground">{t('providers.api_key_hint', { name: provider?.name ?? '' })}</p>
      </div>

      <input
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder={t('providers.placeholder_api_key')}
        className="w-full px-4 py-3 rounded-xl border border-border/40 bg-card text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-foreground transition-colors"
      />

      <div className="border-t border-border/40 pt-6">
        <h2 className="text-base font-semibold text-foreground mb-3">{t('providers.status')}</h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span
              className={`w-2.5 h-2.5 rounded-full ${
                status === 'connected'
                  ? 'bg-green-500'
                  : status === 'failed'
                    ? 'bg-red-500'
                    : status === 'checking'
                      ? 'bg-yellow-500 animate-pulse'
                      : 'bg-muted-foreground/40'
              }`}
            />
            <span className="text-sm text-muted-foreground">
              {status === 'connected'
                ? t('providers.connected')
                : status === 'failed'
                  ? t('providers.connection_failed')
                  : status === 'checking'
                    ? t('providers.checking')
                    : t('providers.not_connected')}
            </span>
          </div>
          <button
            onClick={checkStatus}
            disabled={status === 'checking'}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
          >
            {t('providers.check_connection')}
          </button>
        </div>
      </div>
    </div>
  )
}
