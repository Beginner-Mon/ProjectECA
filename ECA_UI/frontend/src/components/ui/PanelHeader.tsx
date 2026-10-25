import type { ReactNode } from 'react'
import { cn } from '../../lib/utils'

/** Every panel header uses this, so their sizes cannot drift apart. */
export const PANEL_HEADER_ACTION_CLASS =
  'p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors shrink-0'

interface PanelHeaderProps {
  icon: ReactNode
  title: ReactNode
  action?: ReactNode
  className?: string
}

export default function PanelHeader({ icon, title, action, className }: PanelHeaderProps) {
  return (
    <header className={cn('flex items-center gap-2 h-12 px-4 border-b border-border/40 shrink-0', className)}>
      <span className="w-5 h-5 shrink-0 flex items-center justify-center">{icon}</span>
      <h2 className="flex-1 min-w-0 text-sm font-semibold text-foreground tracking-tight truncate">{title}</h2>
      {action}
    </header>
  )
}
