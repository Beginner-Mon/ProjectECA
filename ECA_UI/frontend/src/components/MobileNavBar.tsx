import { useTranslation } from 'react-i18next'
import type { RefObject } from 'react'
import { X } from 'lucide-react'
import type { PanelId, NavItem } from './FloatingNavBar'
import { useAvatarBg } from '../hooks/useAvatarBg'
import AvatarWithLogo from './AvatarWithLogo'

interface MobileNavBarProps {
  activePanel: PanelId
  onIconClick: (id: PanelId) => void
  navItems: NavItem[]
  panelContent?: React.ReactNode
  railRef: RefObject<HTMLDivElement | null>
}

/**
 * MobileNavBar — mobile-only navigation (rendered by FloatingNavBar when
 * `isMobile`). No burger menu, no music toggle, no chat icon by design.
 *
 * - The conversation lives in MobileChatDock (toggle ^ above the pinned
 *   composer). This rail holds the rest, top to bottom in desktop order:
 *   sessions, avatars, motion, then the avatar-profile.
 * - Non-chat panels still open as overlay bottom-sheets (backdrop + tap to
 *   dismiss), exactly like before.
 */
export default function MobileNavBar({
  activePanel,
  onIconClick,
  navItems,
  panelContent,
  railRef,
}: MobileNavBarProps) {
  const { t } = useTranslation()
  const { bg } = useAvatarBg()

  const btnSize = 40

  // Desktop order (FloatingNavBar NAV_ITEMS) minus chat — the
  // conversation toggle lives in MobileChatDock, above the composer.
  const railIds: PanelId[] = ['sessions', 'avatars', 'motion']
  const railItems = railIds
    .map((id) => navItems.find((item) => item.id === id))
    .filter((item): item is NavItem => item !== undefined)

  const railBtnClass = (isActive: boolean) =>
    `flex items-center justify-center rounded-xl transition-colors ${
      isActive
        ? 'bg-secondary text-foreground'
        : 'text-muted-foreground hover:text-foreground hover:bg-secondary/60'
    }`

  return (
    <>
      {/* ── Right-edge vertical rail ── */}
      <div
        ref={railRef}
        className="mobile-nav-rail fixed right-2 top-1/2 -translate-y-1/2 z-[10000] flex flex-col items-center gap-1 p-1.5 rounded-2xl bg-white overflow-y-auto [&>*]:shrink-0"
      >
        {railItems.map((item) => {
          const Icon = item.icon
          return (
            <button
              key={item.id}
              onClick={() => onIconClick(item.id)}
              className={railBtnClass(activePanel === item.id)}
              style={{ width: btnSize, height: btnSize }}
              title={item.label}
              aria-label={item.label}
              aria-pressed={activePanel === item.id}
            >
              <Icon className="w-[18px] h-[18px]" />
            </button>
          )
        })}

        <div className="h-px w-6 bg-border/40" />
        <button
          onClick={() => onIconClick('settings')}
          className={railBtnClass(activePanel === 'settings')}
          style={{ width: btnSize, height: btnSize }}
          title={t('nav.profile_settings')}
          aria-label={t('nav.profile_settings')}
          aria-pressed={activePanel === 'settings'}
        >
          <AvatarWithLogo size="xs" bgClassName={bg.className} logoClassName={bg.logoClassName} />
        </button>
      </div>

      {/* ── Bottom sheet for non-chat panels (chat lives in MobileChatDock) ── */}
      {activePanel && activePanel !== 'chat' && (
        <div className="fixed inset-0 z-[9999] flex items-end">
          <div
            className="absolute inset-0 bg-black/40 backdrop-blur-sm"
            onClick={() => onIconClick(activePanel)}
          />
          <div
            className="relative w-full bg-card rounded-t-2xl animate-slide-up flex flex-col overflow-hidden shadow-[0_-8px_40px_rgba(0,0,0,0.4)] h-auto max-h-[80vh]"
            style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
          >
            <div className="flex items-center px-4 h-14 border-b border-border/40 shrink-0">
              <button
                onClick={() => onIconClick(activePanel)}
                className="p-2 -ml-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors"
                aria-label={t('modal.close')}
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              {panelContent}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
