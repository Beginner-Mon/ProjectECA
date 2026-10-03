import { useTranslation } from 'react-i18next'
import { Coffee } from 'lucide-react'
import { kofiUrl } from '../lib/kofi'
import { isNative } from '../lib/nativeAuth'

const KOFI_URL = kofiUrl(import.meta.env.VITE_KOFI_URL as string | undefined)

/**
 * "Buy me a coffee" link, top-right — mirrors the ECA logo at top-left.
 *
 * Web only: the App Store and Play Store do not allow a native app to send
 * users to an outside tipping page (tips must go through in-app purchase), so
 * the Capacitor build never shows it. Hidden too when VITE_KOFI_URL is unset.
 */
export default function KofiButton({ url = KOFI_URL }: { url?: string | null }) {
  const { t } = useTranslation()
  if (!url || isNative()) return null

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={t('nav.support_kofi')}
      aria-label={t('nav.support_kofi')}
      className="fixed top-5 right-5 z-[9990] flex items-center gap-2 h-9 px-2.5 desktop:px-3.5 rounded-full
        bg-card/70 backdrop-blur-md border border-border/40 shadow-sm
        text-sm font-medium text-foreground opacity-80 transition-all
        hover:opacity-100 hover:bg-card hover:shadow-md"
    >
      {/* Ko-fi's brand red on the cup only; the pill follows the app theme. */}
      <Coffee className="w-4 h-4 text-[#FF5E5B]" aria-hidden />
      <span className="hidden desktop:inline">{t('nav.support_kofi')}</span>
    </a>
  )
}
