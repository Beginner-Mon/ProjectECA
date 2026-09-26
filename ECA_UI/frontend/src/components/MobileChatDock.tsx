import { useId, useRef, useState, type PointerEvent, type RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronsUp, ChevronsDown } from 'lucide-react'
import ChatPanel from './ChatPanel'
import ChatInputBar from './ChatInputBar'
import MobileMotionChips from './MobileMotionChips'
import { clampChatHeight, moveChatDrag } from '../lib/mobileChatLayout'

interface MobileChatDockProps {
  chatOpen: boolean
  onOpenChange: (open: boolean) => void
  maxHeight: number
  dockRef: RefObject<HTMLDivElement | null>
  contentRef: RefObject<HTMLDivElement | null>
}

interface Drag {
  pointerId: number
  startY: number
  startHeight: number
  dragging: boolean
}

/** The handle resizes the conversation; the composer always stays mounted below it. */
export default function MobileChatDock({ chatOpen, onOpenChange, maxHeight, dockRef, contentRef }: MobileChatDockProps) {
  const { t } = useTranslation()
  const contentId = useId()
  const drag = useRef<Drag | null>(null)
  const suppressClick = useRef(false)
  const [dragHeight, setDragHeight] = useState<number | null>(null)
  const [savedHeight, setSavedHeight] = useState<number | null>(null)
  const height = clampChatHeight(dragHeight ?? (chatOpen ? savedHeight ?? maxHeight : 0), maxHeight)
  const expanded = height > 0

  const startDrag = (event: PointerEvent<HTMLButtonElement>) => {
    if (!event.isPrimary || event.button !== 0 || drag.current) return
    suppressClick.current = false
    drag.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      // A gesture interrupting an animation starts at the visible height.
      startHeight: contentRef.current?.getBoundingClientRect().height ?? height,
      dragging: false,
    }
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const moveDrag = (event: PointerEvent<HTMLButtonElement>) => {
    const current = drag.current
    if (!current || current.pointerId !== event.pointerId) return
    const next = moveChatDrag(current.startHeight, current.startY, event.clientY, maxHeight, current.dragging)
    current.dragging = next.dragging
    if (!next.dragging) return
    suppressClick.current = true
    setDragHeight(next.height)
    onOpenChange(next.height > 0)
  }

  const finishDrag = (event: PointerEvent<HTMLButtonElement>) => {
    const current = drag.current
    if (!current || current.pointerId !== event.pointerId) return
    drag.current = null
    if (current.dragging) {
      // Cancel/lost capture commits the last position, not a spurious event coordinate.
      const nextHeight = event.type === 'pointerup'
        ? moveChatDrag(current.startHeight, current.startY, event.clientY, maxHeight, true).height
        : clampChatHeight(dragHeight ?? current.startHeight, maxHeight)
      if (nextHeight > 0) setSavedHeight(nextHeight)
      onOpenChange(nextHeight > 0)
    }
    setDragHeight(null)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  const resizeWithKeyboard = (nextHeight: number) => {
    const clamped = clampChatHeight(nextHeight, maxHeight)
    if (clamped > 0) setSavedHeight(clamped)
    onOpenChange(clamped > 0)
  }

  return (
    <div
      ref={dockRef}
      className="dark block md:hidden fixed bottom-0 inset-x-0 z-40 bg-transparent"
      style={{ paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom, 0px))' }}
    >
      <div className="mobile-chat-conversation mx-3 bg-white border border-border/40 rounded-2xl p-2 flex flex-col">
        <div className="flex justify-center shrink-0">
          <button
            type="button"
            onClick={(event) => {
              // Keyboard clicks have detail=0 and must work even after pointer cancellation.
              if (suppressClick.current && event.detail !== 0) {
                suppressClick.current = false
                return
              }
              onOpenChange(!chatOpen)
            }}
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={finishDrag}
            onPointerCancel={finishDrag}
            onLostPointerCapture={finishDrag}
            onKeyDown={(event) => {
              const next = event.key === 'ArrowUp' ? height + 24
                : event.key === 'ArrowDown' ? height - 24
                  : event.key === 'Home' ? 0
                    : event.key === 'End' ? maxHeight : null
              if (next === null) return
              event.preventDefault()
              resizeWithKeyboard(next)
            }}
            aria-label={t(expanded ? 'chat.hide_conversation' : 'chat.show_conversation')}
            aria-expanded={expanded}
            aria-controls={contentId}
            title={t('chat.resize_conversation')}
            className="mobile-chat-handle w-full h-4 flex items-center justify-center touch-none select-none cursor-ns-resize text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-foreground"
          >
            {expanded ? <ChevronsDown className="size-3.5" /> : <ChevronsUp className="size-3.5" />}
          </button>
        </div>
        <div
          id={contentId}
          ref={contentRef}
          inert={!expanded}
          aria-hidden={!expanded}
          className="mobile-chat-content max-h-[35vh] overflow-hidden flex flex-col"
          style={{ height, maxHeight: `min(35vh, ${maxHeight}px)`, transition: dragHeight !== null ? 'none' : undefined }}
        >
          <MobileMotionChips />
          <div className="flex-1 min-h-0 overflow-hidden">
            <ChatPanel hideInput active={expanded} />
          </div>
        </div>
        <ChatInputBar embedded />
      </div>
    </div>
  )
}
