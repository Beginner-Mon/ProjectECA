import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { ChevronLeft } from 'lucide-react'
import LegalContent from '@/components/LegalContent'
import type { LegalDoc } from '@/legal'

/** Public /terms and /privacy: readable before signing in (SCRUM-41). */
export default function LegalPage({ doc }: { doc: LegalDoc }) {
  const { t } = useTranslation()
  const navigate = useNavigate()

  return (
    <div className="min-h-screen w-full bg-background flex justify-center">
      <div className="w-full max-w-3xl flex flex-col">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border/40">
          <button
            onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/login'))}
            className="p-1.5 -ml-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors"
            aria-label={t('modal.back')}
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <h1 className="text-base font-semibold text-foreground">{t('legal.title')}</h1>
        </div>
        {/* Keyed so following a link from /terms to /privacy opens the right tab. */}
        <LegalContent key={doc} initialDoc={doc} />
      </div>
    </div>
  )
}
