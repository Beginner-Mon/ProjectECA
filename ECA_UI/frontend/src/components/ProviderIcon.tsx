import { Package } from 'lucide-react'
import { catalogLogoUrl, providerIconUrl } from '@/lib/providerIcons'

interface ProviderIconProps {
  presetId: string
  /** Set for a provider read from the catalog: its logo comes from there. */
  catalogId?: string
  className?: string
}

/**
 * A provider's own logo, or a neutral box when it has none.
 *
 * Painted as a CSS mask, not an <img>: the marks are single-colour shapes, and
 * a mask filled with `currentColor` takes the text colour. So a logo follows
 * the theme and the card's hover colour exactly like the lucide icons beside
 * it — an <img> would stay black on the dark theme.
 */
export default function ProviderIcon({ presetId, catalogId, className }: ProviderIconProps) {
  const url = providerIconUrl(presetId) ?? (catalogId ? catalogLogoUrl(catalogId) : undefined)
  if (!url) return <Package className={className} />

  const mask = `url("${url}") center / contain no-repeat`
  return (
    <span
      aria-hidden="true"
      className={`inline-block ${className ?? ''}`}
      style={{ backgroundColor: 'currentColor', mask, WebkitMask: mask }}
    />
  )
}
