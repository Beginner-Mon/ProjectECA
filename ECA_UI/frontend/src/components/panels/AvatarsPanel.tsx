import { useEffect, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { motion } from 'framer-motion'
import { UserRound, Check, TriangleAlert, Star, RefreshCw, Loader2 } from 'lucide-react'
import { ScrollArea } from '../ui/scroll-area'
import { Button } from '../ui/button'
import { useMotion } from '../../hooks/useMotion'
import { incompatibilityReason, type Character } from '../../lib/characters'
import { usePreferences } from '../../hooks/usePreferences'

/** [nền A, nền B + blob phụ, accent blob] */
const MESH_PALETTE: Array<[string, string, string]> = [
  ['#7c3aed', '#a855f7', '#e879f9'], // violet
  ['#0891b2', '#2563eb', '#22d3ee'], // cyan → blue
  ['#e11d48', '#ec4899', '#fb7185'], // rose
  ['#d97706', '#ea580c', '#fbbf24'], // amber
  ['#059669', '#0d9488', '#34d399'], // emerald
  ['#4f46e5', '#9333ea', '#818cf8'], // indigo
]

/** FNV-1a. Colours hang off the slug, not the list position — reordering the
 *  catalog used to repaint every character. */
function hashSlug(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

/** Two radial blobs over a full-cover base gradient, so every character gets a
 *  distinct grain even while they all share the same placeholder logo. Inline
 *  because Tailwind cannot emit per-slug gradient positions. */
function meshStyle(slug: string): CSSProperties {
  const h = hashSlug(slug)
  const [a, b, c] = MESH_PALETTE[h % MESH_PALETTE.length]
  // Blobs are forced into opposite halves; left to themselves they overlap into
  // a single smear and every card looks the same again. `flip` decides which
  // half the bright accent takes, otherwise every card lights up top-left.
  const flip = (h >>> 27) & 1
  const near = { x: 8 + ((h >>> 3) % 34), y: 4 + ((h >>> 9) % 36) } //  8–42% / 4–40%
  const far = { x: 58 + ((h >>> 15) % 34), y: 56 + ((h >>> 21) % 36) } // 58–92% / 56–92%
  const accent = flip ? far : near
  const wash = flip ? near : far
  return {
    backgroundImage: [
      `radial-gradient(95% 75% at ${accent.x}% ${accent.y}%, ${c} 0%, transparent 58%)`,
      `radial-gradient(110% 85% at ${wash.x}% ${wash.y}%, ${b} 0%, transparent 62%)`,
      `linear-gradient(${flip ? 320 : 140}deg, ${a} 0%, ${b} 100%)`,
    ].join(', '),
  }
}

/**
 * Display names used to come from a hardcoded map here, which is why `anne`
 * had none and fell through to a title-cased filename. They live on the
 * character record now, so adding a character no longer means editing this file.
 */
function AvatarCard({
  slug,
  displayName,
  thumbnailUrl,
  disabledReason,
  isSelected,
  isDefault,
  isSwitching,
  onSetDefault,
  onClick,
}: {
  slug: string
  displayName: string
  thumbnailUrl: string | null
  disabledReason: string | null
  isSelected: boolean
  isDefault: boolean
  isSwitching?: boolean
  onSetDefault: () => void
  onClick: () => void
}) {
  const { t } = useTranslation()
  const [imgFailed, setImgFailed] = useState(false)
  const disabled = disabledReason !== null
  const showSwitching = !!isSwitching

  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      aria-pressed={isSelected}
      title={disabledReason ?? displayName}
      className="
        group relative w-full aspect-[5/6] rounded-xl cursor-pointer
        disabled:cursor-not-allowed
        transition-shadow duration-200
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring
        focus-visible:ring-offset-2 focus-visible:ring-offset-card
      "
    >
      {/* Clip layer — anything painted inside is guaranteed to follow the radius.
          The old label sat directly on the button with `backdrop-blur`, which
          Chromium refuses to clip against an ancestor's border-radius; that is
          what made the name bar bleed out of the bottom corners. */}
      <div className="absolute inset-0 rounded-[inherit] overflow-hidden">
        <div className={`absolute inset-0 ${disabled ? 'grayscale opacity-60' : ''}`}>
          {/* Mesh + logo: the placeholder layer, always present. A real
              thumbnail is painted on top of it, so it doubles as the
              while-loading state without any extra bookkeeping. */}
          <div
            style={meshStyle(slug)}
            className="
              absolute inset-0 flex items-center justify-center
              transition-transform duration-500 ease-out group-hover:scale-110
            "
          >
            {/* eca-logo.svg is 638×543 with `meet`, so in a square box it fits by
                width and pads top/bottom. 114% of the card width reproduces the
                old `w-40` on a 140px card at any card size — do not "fix" it. */}
            <img
              src="/eca-logo.svg"
              alt=""
              className="
                w-[114%] h-auto max-w-none select-none
                opacity-30 group-hover:opacity-45 transition-opacity duration-200
              "
              style={{ filter: 'brightness(0) invert(1)' }}
            />
          </div>

          {/* No thumbnails exist yet — characters.thumbnail_url is null for every
              row — but when they land they cover the placeholder. */}
          {thumbnailUrl && !imgFailed && (
            <img
              src={thumbnailUrl}
              alt=""
              loading="lazy"
              onError={() => setImgFailed(true)}
              className="absolute inset-0 w-full h-full object-cover select-none"
            />
          )}
        </div>

        {/* Scrim + name. Gradient, not a blurred bar: see the clip-layer note. */}
        <div
          className={`
            absolute inset-x-0 bottom-0 pt-6 pb-2 px-2 text-center
            bg-gradient-to-t from-black/85 via-black/60 to-transparent
            transition-all duration-200 ease-out
            ${disabled
              ? 'translate-y-0 opacity-100'
              : `translate-y-full opacity-0
                 group-hover:translate-y-0 group-hover:opacity-100
                 group-focus-visible:translate-y-0 group-focus-visible:opacity-100`
            }
          `}
        >
          <span className="text-xs font-medium text-white truncate block">
            {displayName}
          </span>
          {disabledReason && (
            <span className="mt-0.5 flex items-start justify-center gap-1 text-[10px] text-white/70">
              <TriangleAlert className="w-3 h-3 shrink-0 mt-px" />
              <span className="line-clamp-2 text-left">{disabledReason}</span>
            </span>
          )}
        </div>
      </div>

      {/* Rings live on top of the artwork and are inset, so they track the radius
          instead of floating outside the border box the way `ring-2` did. */}
      <span
        className={`
          absolute inset-0 rounded-[inherit] pointer-events-none transition-all duration-200
          ${isSelected
            ? 'ring-2 ring-inset ring-primary'
            : disabled ? '' : 'ring-0 ring-inset ring-white/40 group-hover:ring-1'
          }
        `}
      />

      {isSelected && (
        <span
          className="
            absolute top-2 right-2 w-5 h-5 rounded-full
            bg-primary text-primary-foreground
            flex items-center justify-center shadow-sm pointer-events-none
          "
        >
          <Check className="w-3 h-3" strokeWidth={3} />
        </span>
      )}

      {isDefault && (
        <span className="absolute top-2 left-2 flex items-center gap-1 rounded-full bg-amber-500 text-white text-[10px] font-semibold px-2 py-0.5 shadow-sm pointer-events-none">
          <Star className="w-3 h-3" /> {t('avatars.default_badge')}
        </span>
      )}

      {isSelected && !isDefault && !disabled && !showSwitching && (
        <motion.span
          role="button"
          tabIndex={0}
          initial="initial"
          whileHover="hover"
          whileTap={{ scale: 0.95 }}
          onClick={(e) => {
            e.stopPropagation()
            onSetDefault()
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              e.stopPropagation()
              onSetDefault()
            }
          }}
          className="absolute top-2 left-2 flex items-center gap-1 rounded-full bg-primary text-primary-foreground text-[10px] font-semibold px-2 py-0.5 shadow-sm cursor-pointer overflow-hidden"
        >
          <span className="relative z-10">{t('avatars.set_default')}</span>
          <motion.span
            className="absolute inset-0 bg-gradient-to-r from-transparent via-white/35 to-transparent -skew-x-12"
            variants={{
              initial: { x: '-100%' },
              hover: { x: '100%' },
            }}
            transition={{ duration: 0.55, ease: 'easeInOut' }}
          />
        </motion.span>
      )}

      {/* B3: unified violet switching — miniature of LoadingOverlay (same violet glow + border).
          Lag is only in Canvas, but this DOM overlay still spins while Canvas is frozen. */}
      {showSwitching && (
        <span className="absolute inset-0 rounded-[inherit] bg-background/60 backdrop-blur-[2px] flex items-center justify-center pointer-events-none">
          <span className="relative flex items-center justify-center">
            <span className="absolute inset-0 rounded-full blur-md bg-violet-500/30 animate-pulse" />
            <span className="relative flex h-10 w-10 items-center justify-center rounded-full border border-violet-500/20 bg-background/40 shadow-[0_0_12px_rgba(139,92,246,0.15)]">
              <Loader2 className="w-5 h-5 animate-spin text-violet-500" />
            </span>
          </span>
        </span>
      )}
    </button>
  )
}

/** Fallback skeleton count until GET /characters returns `total`.
 *  Hardcoding 4 breaks when characters are added (review 07-09-2026). 6 covers the
 *  current 4 plus near-term growth; after the first fetch we know the real count. */
const SKELETON_FALLBACK = 6

/** B2 reverted (07-09-2026): skeleton stays uniform gray, not per-character mesh.
 *  Mesh colors are identity (violet/cyan/rose/emerald per slug) — showing them
 *  in skeleton misleads user into thinking content is loaded. Gray = clearly loading.
 *  B5: aria-label/sr-only now via i18n (avatars.loading), not hardcoded English. */
function AvatarGridSkeleton({ count, label }: { count: number; label: string }) {
  return (
    <div
      className="grid grid-cols-2 gap-3"
      role="status"
      aria-busy="true"
      aria-label={label}
    >
      {Array.from({ length: count }, (_, i) => (
        <div
          key={i}
          className="w-full aspect-[5/6] rounded-xl bg-secondary/60 animate-pulse"
        />
      ))}
      <span className="sr-only">{label}</span>
    </div>
  )
}

export default function AvatarsPanel() {
  const { t } = useTranslation()
  const {
    selectedVrmId,
    setSelectedVrmId,
    vrmOptions,
    vrmOptionsLoading,
    vrmOptionsError,
    catalogLoading,
    catalogError,
    switchingId,
    ensureCatalogLoaded,
  } = useMotion()

  // Lazy: only when panel mounts does it need the lite cards (not on initial web load)
  useEffect(() => {
    void ensureCatalogLoaded()
  }, [ensureCatalogLoaded])

  // B1: combine bootstrap + catalog loading — either means the grid is not ready.
  // Before fix only vrmOptionsLoading was checked, so the lazy GET /characters had no skeleton.
  // B3: switching does NOT affect grid loading — grid stays visible with mini overlay on switching card.
  const isLoading = vrmOptionsLoading || catalogLoading
  // Show catalog error if present, otherwise bootstrap error (both mean "cannot show grid")
  const isError = catalogError ?? vrmOptionsError
  // Skeleton count: if we already have options use its length (covers future growth after first fetch),
  // otherwise fallback. Math.max ensures we never show fewer skeletons than the current grid would need.
  const skeletonCount = vrmOptions.length > 1 ? vrmOptions.length : SKELETON_FALLBACK

  // The star reads from the same fetch everything else does, and writing to it
  // is one optimistic call — no session check, no version to carry, and nothing
  // to reconcile if two devices set a default at once (last write wins).
  const { data: prefs, patch } = usePreferences()
  const defaultSlug = prefs?.preferences.selected_character_slug ?? null

  const handleSetDefault = (slug: string) => {
    if (!prefs) return // guest: nothing to sync to
    patch({ selected_character_slug: slug })
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-border/40 shrink-0">
        <h2 className="text-sm font-semibold text-foreground tracking-tight flex items-center gap-2">
          <UserRound className="w-4 h-4 text-primary" />
          {t('avatars.title')}
        </h2>
        <p className="text-[11px] text-muted-foreground mt-0.5">{t('avatars.subtitle')}</p>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4">
          {/* B1+B5: skeletons cover BOTH bootstrap and lazy catalog. Same grid as real cards so nothing jumps.
              Count is dynamic (SKELETON_FALLBACK until we know total) — adding characters no longer desyncs.
              B5: label via i18n (avatars.loading) — CharacterViewer already does this for Canvas. */}
          {isLoading && <AvatarGridSkeleton count={skeletonCount} label={t('avatars.loading')} />}

          {/* B4: named error + retry — empty grid and failed fetch look identical otherwise.
              Retry calls ensureCatalogLoaded() again (hasLite is still false, catalogLoading guards double-click). */}
          {!isLoading && isError && (
            <div className="flex flex-col items-center justify-center py-8 gap-3 text-center px-4">
              <div className="w-10 h-10 rounded-full bg-destructive/10 flex items-center justify-center">
                <TriangleAlert className="w-5 h-5 text-destructive/70" />
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium text-destructive">{t('avatars.load_failed')}</p>
                <p className="text-[11px] text-muted-foreground line-clamp-2 break-words">{isError}</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void ensureCatalogLoaded()}
                className="mt-1 gap-1.5"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                {t('common.refresh')}
              </Button>
            </div>
          )}

          {!isLoading && !isError && vrmOptions.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-4">{t('avatars.empty')}</p>
          )}

          {!isLoading && !isError && vrmOptions.length > 0 && (
            <div className="grid grid-cols-2 gap-3">
              {vrmOptions.map((option) => {
                const character: Character | undefined = option.character
                const isSwitching = switchingId === option.id
                return (
                  <AvatarCard
                    key={option.id}
                    slug={option.id}
                    displayName={option.label}
                    thumbnailUrl={character?.thumbnail_url ?? null}
                    disabledReason={character ? incompatibilityReason(character) : null}
                    isSelected={selectedVrmId === option.id}
                    isDefault={defaultSlug === option.id}
                    isSwitching={isSwitching}
                    onSetDefault={() => handleSetDefault(option.id)}
                    onClick={() => setSelectedVrmId(option.id)}
                  />
                )
              })}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

