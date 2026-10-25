import { useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Loader2 } from 'lucide-react'
import { ScrollArea } from './ui/scroll-area'
import PanelHeader, { PANEL_HEADER_ACTION_CLASS } from './ui/PanelHeader'
import ChatMessage, { ErrorNotice } from './ChatMessage'
import ChatDivider from './ChatDivider'
import ChatInputBar from './ChatInputBar'
import { useChat } from '../hooks/useChat'

/* ─── ChatPanel: header + message list + composer ───
 *
 * `hideInput` is for the mobile dock (MobileChatDock): the composer is
 * pinned separately below the message list there, so rendering it here too
 * would stack two composers. Everywhere else the full panel renders.
 */
export default function ChatPanel({ hideInput = false, active = true }: { hideInput?: boolean; active?: boolean }) {
  const {
    messages,
    isTyping,
    isGenerating,
    stageLabel,
    isSwitching,
    sessionLoadError,
    retrySessionLoad,
    ui,
    startNewSession,
  } = useChat()

  const { t } = useTranslation()

  const bottomRef = useRef<HTMLDivElement>(null)

  /* auto-scroll on new messages */
  useEffect(() => {
    if (!active) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping, stageLabel, active])

  return (
    <div className={`flex flex-col h-full desktop:border-r border-border/40 ${hideInput ? 'bg-transparent' : 'bg-card relative z-10'}`}>
      {/* ── Header ── */}
      <PanelHeader
        className="hidden desktop:flex bg-card"
        icon={<img src="/eca-logo.svg" alt="" className="w-5 h-5 object-contain select-none dark:invert" />}
        title={t('chat.title')}
        action={
          /* Without this the conversation restored on load is the only one the
             user can ever be in — there is no other way out of it yet. */
          <button
            onClick={startNewSession}
            title={t('chat.new_conversation')}
            className={PANEL_HEADER_ACTION_CLASS}
          >
            <Plus className="w-5 h-5" />
          </button>
        }
      />

      {/* ── Messages ── */}
      <ScrollArea className="flex-1 min-h-0 px-2">
        <div className="pt-2 pb-0 desktop:pt-4 desktop:pb-0 space-y-1 desktop:space-y-2 max-w-full overflow-x-hidden">
          {isSwitching ? (
            <div className="flex flex-col items-center justify-center py-16 gap-2 text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin" />
              <p className="text-xs animate-pulse">{t('common.wait')}</p>
            </div>
          ) : sessionLoadError ? (
            // The conversation exists but could not be read. Not the greeting
            // (it would pass an old conversation off as new) and not blank.
            <div className="px-3 desktop:px-5 py-2 desktop:py-3 animate-message-in w-full max-w-full">
              <ErrorNotice text={ui.error_system} onRetry={() => void retrySessionLoad()} />
            </div>
          ) : (
            <>
              {messages.map((msg, i) => {
                if (msg.kind === 'divider' || msg.role === 'system') {
                  return <ChatDivider key={msg.id} toLabel={msg.dividerMeta?.toLabel} />
                }
                return <ChatMessage key={msg.id} message={msg} isStreaming={isGenerating && i === messages.length - 1} />
              })}

              {/* typing / stage indicator — text pulse từ backend, không Loader2 */}
              {(isTyping || stageLabel) && (
                <div className="px-3 desktop:px-5 py-2 desktop:py-3 animate-message-in">
                  {stageLabel ? (
                    <p className="text-xs text-muted-foreground italic animate-pulse">{stageLabel}</p>
                  ) : (
                    <div className="flex gap-1.5">
                      <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.3s]" />
                      <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 animate-bounce [animation-delay:-0.15s]" />
                      <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50 animate-bounce" />
                    </div>
                  )}
                </div>
              )}
            </>
          )}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      {/* ── Input (hidden in the mobile dock — the dock pins its own
          composer below the list) ── */}
      {!hideInput && <ChatInputBar />}
    </div>
  )
}
