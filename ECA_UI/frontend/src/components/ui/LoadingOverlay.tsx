import { Loader2 } from 'lucide-react'

interface LoadingOverlayProps {
  text?: string
  fullScreen?: boolean
  /** Failure state: the text is an error, the spinner stops, and `onRetry` adds a button. */
  isError?: boolean
  onRetry?: () => void
  retryLabel?: string
}

export default function LoadingOverlay({ text = 'Loading...', fullScreen = false, isError = false, onRetry, retryLabel }: LoadingOverlayProps) {
  const containerClasses = fullScreen
    ? "fixed inset-0 w-screen h-screen z-[9999] flex flex-col items-center justify-center bg-background/80 backdrop-blur-md"
    : "absolute inset-0 z-[9999] flex flex-col items-center justify-center bg-background/80 backdrop-blur-md pointer-events-none"

  return (
    <div className={containerClasses}>
      <div className="flex flex-col items-center gap-5">
        {/* No spinner once loading has failed: it would suggest work in progress. */}
        {!isError && (
          <div className="relative flex items-center justify-center">
            {/* Subtle glow effect behind spinner */}
            <div className="absolute inset-0 rounded-full blur-xl bg-violet-500/30 opacity-70 animate-pulse" />

            {/* Glassmorphism circle holding the spinner */}
            <div className="relative flex h-16 w-16 items-center justify-center rounded-full border-2 border-violet-500/20 bg-background/40 shadow-[0_0_20px_rgba(139,92,246,0.15)]">
              <Loader2 className="w-8 h-8 animate-spin text-violet-500 drop-shadow-[0_0_8px_rgba(139,92,246,0.6)]" />
            </div>
          </div>

        )}

        {/* Animated loading text */}
        {/* text-center + side padding: a long label wraps on a phone, and its
            lines used to sit flush left (03-10). */}
        <span className={`max-w-full px-6 text-center text-sm font-medium text-foreground/80 tracking-[0.2em] uppercase ${isError ? '' : 'animate-pulse'}`}>
          {text}
        </span>
        {isError && onRetry && (
          // The overlay itself ignores the pointer so the scene behind stays
          // usable while loading; the one button on it must not.
          <button
            type="button"
            onClick={onRetry}
            className="pointer-events-auto rounded-lg border border-border bg-background/60 px-4 py-1.5 text-sm font-medium text-foreground hover:bg-secondary/60 transition-colors"
          >
            {retryLabel}
          </button>
        )}
      </div>
    </div>
  )
}
