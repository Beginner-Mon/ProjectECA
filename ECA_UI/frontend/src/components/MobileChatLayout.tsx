import { useLayoutEffect, useRef, useState } from 'react'
import type { NavItem, PanelId } from './FloatingNavBar'
import MobileChatDock from './MobileChatDock'
import MobileNavBar from './MobileNavBar'
import { chatHeightLimit, chatRailTop, dockRightInset, isLandscape } from '../lib/mobileChatLayout'

interface MobileChatLayoutProps {
  activePanel: PanelId
  onIconClick: (id: PanelId) => void
  onChatOpenChange: (open: boolean) => void
  navItems: NavItem[]
  panelContent?: React.ReactNode
}

/** Measures the actual animated dock so the rail follows it without a second animation. */
export default function MobileChatLayout({ activePanel, onIconClick, onChatOpenChange, navItems, panelContent }: MobileChatLayoutProps) {
  const dockRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const railRef = useRef<HTMLDivElement>(null)
  const insetRef = useRef<HTMLDivElement>(null)
  const [maxHeight, setMaxHeight] = useState(() => window.innerHeight * 0.35)

  useLayoutEffect(() => {
    const dock = dockRef.current
    const content = contentRef.current
    const rail = railRef.current
    if (!dock || !content || !rail) return

    const viewport = window.visualViewport
    const measure = () => {
      const viewportTop = viewport?.offsetTop ?? 0
      const viewportHeight = viewport?.height ?? window.innerHeight
      const topInset = insetRef.current?.getBoundingClientRect().height ?? 0
      // Sideways: rail beside the dock, not stacked above it (see isLandscape).
      const landscape = isLandscape(window.innerWidth, window.innerHeight)
      dock.style.right = landscape ? `${dockRightInset(rail.getBoundingClientRect().width)}px` : ''
      // Keep the composer above keyboards that resize only the visual viewport.
      dock.style.bottom = `${Math.max(0, window.innerHeight - viewportTop - viewportHeight)}px`
      const dockBounds = dock.getBoundingClientRect()
      const contentHeight = content.getBoundingClientRect().height
      const baseHeight = dockBounds.height - contentHeight
      // On very short screens the rail itself can scroll; its buttons keep their
      // size. Beside the dock it may use the full height; above it, what the
      // closed dock leaves.
      rail.style.maxHeight = `${Math.max(40, viewportHeight - (landscape ? 0 : baseHeight) - topInset - 16)}px`
      const railHeight = rail.getBoundingClientRect().height
      setMaxHeight(chatHeightLimit(window.innerHeight, viewportHeight, baseHeight, landscape ? 0 : railHeight, topInset))
      rail.style.translate = 'none'
      // Above the dock whenever they share the right edge — closed too: a
      // centred rail used to overlap a closed dock on short screens (audit 30-09).
      rail.style.top = `${chatRailTop(viewportTop, viewportHeight, railHeight, landscape ? null : dockBounds.top, topInset)}px`
    }
    const observer = new ResizeObserver(measure)
    observer.observe(dock)
    observer.observe(content)
    observer.observe(rail)
    const frame = requestAnimationFrame(measure)
    window.addEventListener('resize', measure)
    viewport?.addEventListener('resize', measure)
    viewport?.addEventListener('scroll', measure)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', measure)
      viewport?.removeEventListener('resize', measure)
      viewport?.removeEventListener('scroll', measure)
    }
  }, [])

  return (
    <>
      <div ref={insetRef} aria-hidden className="fixed invisible pointer-events-none" style={{ height: 'env(safe-area-inset-top, 0px)' }} />
      <MobileChatDock
        chatOpen={activePanel === 'chat'}
        onOpenChange={onChatOpenChange}
        maxHeight={maxHeight}
        dockRef={dockRef}
        contentRef={contentRef}
      />
      <MobileNavBar
        activePanel={activePanel}
        onIconClick={onIconClick}
        navItems={navItems}
        panelContent={panelContent}
        railRef={railRef}
      />
    </>
  )
}
