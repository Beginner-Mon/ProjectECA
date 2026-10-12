import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useLocale } from '@/hooks/useLocale'
import { LEGAL_DOCS, legalMarkdown, type LegalDoc } from '@/legal'

/**
 * Terms of Service and Privacy Policy, with a switch between the two. Used in
 * Settings and on the public /terms and /privacy pages, so a visitor can read
 * both before creating an account. Follows the site language.
 */
export default function LegalContent({ initialDoc = 'terms' }: { initialDoc?: LegalDoc }) {
  const { t } = useTranslation()
  const { locale } = useLocale()
  const [doc, setDoc] = useState<LegalDoc>(initialDoc)

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div role="tablist" className="flex gap-2 mb-6">
        {LEGAL_DOCS.map((d) => (
          <button
            key={d}
            role="tab"
            aria-selected={doc === d}
            onClick={() => setDoc(d)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
              doc === d
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:text-foreground hover:bg-secondary/60'
            }`}
          >
            {t(`legal.${d}`)}
          </button>
        ))}
      </div>
      <div className="prose dark:prose-invert prose-headings:text-foreground prose-strong:text-foreground prose-a:text-foreground max-w-none text-sm text-foreground/90">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{legalMarkdown(doc, locale)}</ReactMarkdown>
      </div>
    </div>
  )
}
