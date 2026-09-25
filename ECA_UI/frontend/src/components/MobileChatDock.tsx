import { useTranslation } from 'react-i18next'
import { ChevronsUp, ChevronsDown } from 'lucide-react'
import type { PanelId } from './FloatingNavBar'
import ChatPanel from './ChatPanel'
import ChatInputBar from './ChatInputBar'
import MobileMotionChips from './MobileMotionChips'

interface MobileChatDockProps {
  /** `activePanel === 'chat'` means the message list is expanded. */
  chatOpen: boolean
  onToggleChat: (id: PanelId) => void
}

/**
 * MobileChatDock — the mobile bottom cluster (rendered by FloatingNavBar's
 * mobile branch).
 *
 * The chevron and the message list are ONE expandable cluster: the centered
 * lucide chevron sits at the TOP of the cluster, and tapping it pulls the
 * whole cluster (chevron + 40vh list) up with a slide-up animation — or
 * collapses it back down to just the chevron resting on the composer.
 * No backdrop, NOT an overlay sheet like sessions/avatars/motion.
 *
 * Bottom-up when open: [v chevron] [motion chips + 35vh messages]
 * [composer, always visible].
 */
export default function MobileChatDock({ chatOpen, onToggleChat }: MobileChatDockProps) {
  const { t } = useTranslation()

  // `dark` scope: the black scrim is identical in both app themes, so the
  // whole dock subtree always resolves dark-theme tokens (light text on
  // dark) — readable on bg-black/15 whether the app is light or dark.
  // The project declares dark as `@custom-variant dark (&:is(.dark *))`,
  // so this class flips exactly this subtree and nothing else.
  return (
    <div className="dark block md:hidden fixed bottom-0 inset-x-0 z-40">
      {/* ── Expandable cluster: chevron on top, list below it ── */}
      {chatOpen ? (
        <div className="bg-black/15 rounded-t-2xl border-t border-border/40 overflow-hidden animate-slide-up flex flex-col relative z-0">
          <div className="flex justify-center shrink-0 border-b border-border/40">
            <button
              onClick={() => onToggleChat('chat')}
              aria-label={t('chat.hide_conversation')}
              aria-expanded
              title={t('chat.hide_conversation')}
              className="px-8 text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronsDown className="w-5 h-5" />
            </button>
          </div>
          <div className="h-[35vh] max-h-[35vh] overflow-hidden flex flex-col">
            <MobileMotionChips />
            <div className="flex-1 min-h-0 overflow-hidden">
              <ChatPanel hideInput />
            </div>
          </div>
        </div>
      ) : (
        /* Collapsed: only the chevron, resting directly on the composer. */
        <div className="flex justify-center bg-black/15 border-t border-border/40">
          <button
            onClick={() => onToggleChat('chat')}
            aria-label={t('chat.show_conversation')}
            aria-expanded={false}
            title={t('chat.show_conversation')}
            className="px-8 text-muted-foreground hover:text-foreground transition-colors"
          >
            <ChevronsUp className="w-5 h-5" />
          </button>
        </div>
      )}

      {/* ── Composer, always visible. Transparent like the message list
          (option 1: both transparent), and z-20 so it always paints above
          the conversation frame (ChatPanel root is z-10). ── */}
      <div className="bg-black/15 relative z-20" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
        <ChatInputBar />
      </div>
    </div>
  )
}
