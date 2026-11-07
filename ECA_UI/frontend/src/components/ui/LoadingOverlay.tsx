import { useEffect, useState, type CSSProperties } from 'react'
import {
  REVEAL_MS, RISE_TAU_MS, SETTLE_MS, SURGE_MS, WAIT_LEVEL,
  beginFill, endFill, fillLevel,
} from '../../lib/waterFill'

interface LoadingOverlayProps {
  text?: string
  fullScreen?: boolean
  /** Still waiting. When it turns false the water surges to full, settles, and
   *  the overlay fades out, revealing what is behind — then unmounts itself.
   *  Callers keep it mounted and flip this, rather than unmounting it. */
  active?: boolean
}

type Phase = 'waiting' | 'surging' | 'settling' | 'revealing' | 'hidden'

/**
 * Loading screen: the ECA logo filling with water on black.
 *
 * The level is not download progress (lib/waterFill.ts): it creeps up slowly
 * and surges to full when loading is done.
 *
 * Built so it cannot slow the load it is covering. Everything that moves is a
 * CSS transform on an ordinary element, which the browser animates on the
 * compositor — the water keeps moving while the main thread is busy parsing
 * the avatar, and costs that work nothing. No canvas, no second WebGL context,
 * no per-frame JavaScript, and no backdrop blur over the 3D scene (the old
 * spinner had one). The logo shape is a CSS mask (public/eca-logo.svg).
 */
export default function LoadingOverlay({
  text = 'Loading...',
  fullScreen = false,
  active = true,
}: LoadingOverlayProps) {
  const [phase, setPhase] = useState<Phase>(active ? 'waiting' : 'hidden')
  const [prevActive, setPrevActive] = useState(active)
  // Level shown before the slow rise starts, and how long that rise runs.
  const [rise, setRise] = useState<{ from: number; ms: number; started: boolean }>(
    { from: 0, ms: 0, started: false },
  )

  // Adjust phase when `active` flips (state from props, during render).
  if (active !== prevActive) {
    setPrevActive(active)
    setPhase(active ? 'waiting' : phase === 'hidden' ? 'hidden' : 'surging')
  }

  // Waiting: join the page's fill clock, start where it is, then rise slowly.
  useEffect(() => {
    if (phase !== 'waiting') return
    const startedAt = beginFill(performance.now())
    const elapsed = performance.now() - startedAt
    // ~98% of the way to WAIT_LEVEL by the end of the transition.
    const total = -RISE_TAU_MS * Math.log(0.02)
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      setRise({ from: fillLevel(elapsed), ms: Math.max(1500, total - elapsed), started: false })
      // A second frame, so the start level is painted before the transition.
      raf2 = requestAnimationFrame(() => setRise((r) => ({ ...r, started: true })))
    })
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      endFill(performance.now())
    }
  }, [phase])

  // Done: surge → settle → reveal → gone.
  useEffect(() => {
    const next: Partial<Record<Phase, [Phase, number]>> = {
      surging: ['settling', SURGE_MS],
      settling: ['revealing', SETTLE_MS],
      revealing: ['hidden', REVEAL_MS],
    }
    const step = next[phase]
    if (!step) return
    const timer = setTimeout(() => setPhase(step[0]), step[1])
    return () => clearTimeout(timer)
  }, [phase])

  if (phase === 'hidden') return null

  const level = phase === 'waiting'
    ? (rise.started ? WAIT_LEVEL * 0.98 : rise.from)
    : 1
  const levelStyle: CSSProperties = {
    // 100% = surface below the logo, about -4% = covering it, waves included.
    transform: `translateY(${(1 - level) * 104 - 4}%)`,
    transition: phase === 'waiting'
      ? (rise.started ? `transform ${rise.ms}ms cubic-bezier(0.1, 0.55, 0.3, 1)` : 'none')
      : `transform ${SURGE_MS}ms cubic-bezier(0.3, 0, 0.2, 1)`,
  }
  const revealing = phase === 'revealing'

  return (
    <div
      className={`${fullScreen ? 'fixed inset-0 w-screen h-screen' : 'absolute inset-0'} z-[9999] flex flex-col items-center justify-center bg-black ${fullScreen ? '' : 'pointer-events-none'}`}
      style={{ opacity: revealing ? 0 : 1, transition: `opacity ${REVEAL_MS}ms ease-out` }}
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-col items-center gap-6">
        <div
          className="water-logo relative w-[min(44vw,200px)] aspect-[638/543]"
          style={{
            transform: revealing ? 'scale(1.08)' : 'scale(1)',
            transition: `transform ${REVEAL_MS}ms ease-out`,
          }}
        >
          {/* The empty logo, so its shape reads before any water is in it. */}
          <div className="water-mask absolute inset-0 bg-white/10" />
          <div className="water-mask absolute inset-0 overflow-hidden">
            <div className="absolute inset-0" style={levelStyle}>
              <div className={phase === 'settling' ? 'water-settle absolute inset-0' : 'absolute inset-0'}>
                <Wave className="water-drift-slow opacity-60" fill="#6d28d9" offset="5%" />
                <Wave className="water-drift" fill="url(#eca-water)" offset="0%" gradient />
              </div>
            </div>
          </div>
        </div>
        <span className="max-w-full px-6 text-center text-sm font-medium text-white/70 tracking-[0.2em] uppercase">
          {text}
        </span>
      </div>
    </div>
  )
}

/** One wave layer: twice as wide as the logo, drifting left by half its width
 *  forever, so the pattern repeats seamlessly. */
function Wave({ className, fill, offset, gradient = false }: {
  className: string; fill: string; offset: string; gradient?: boolean
}) {
  return (
    <svg
      className={`absolute left-0 w-[200%] h-[112%] ${className}`}
      style={{ top: `calc(-8% + ${offset})` }}
      viewBox="0 0 200 112"
      preserveAspectRatio="none"
      aria-hidden
    >
      {gradient && (
        <defs>
          <linearGradient id="eca-water" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#60a5fa" />
            <stop offset="45%" stopColor="#a855f7" />
            <stop offset="100%" stopColor="#f472b6" />
          </linearGradient>
        </defs>
      )}
      <path d={WAVE_PATH} fill={fill} />
    </svg>
  )
}

/** Eight crests across 200 units (four per logo width), body down to 112. */
const WAVE_PATH = (() => {
  const amp = 3
  const base = 6
  const len = 25
  let d = `M0 ${base}`
  for (let x = 0; x < 200; x += len) {
    d += ` Q${x + len / 4} ${base - amp} ${x + len / 2} ${base}`
    d += ` Q${x + (3 * len) / 4} ${base + amp} ${x + len} ${base}`
  }
  return `${d} L200 112 L0 112 Z`
})()
