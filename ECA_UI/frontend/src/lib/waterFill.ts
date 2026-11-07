/**
 * Timing for the water-fill loading screen (components/ui/LoadingOverlay.tsx).
 *
 * The level is NOT download progress. It creeps up on a curve that approaches
 * WAIT_LEVEL and never reaches it, so a slow load never looks finished; when
 * the thing being waited for is ready, the overlay surges the rest of the way.
 *
 * One clock for the whole page load. The app-start loader (AuthGuard) is
 * replaced by the avatar loader (CharacterViewer) in the same render; with a
 * clock each, the water would drain and start again between the two.
 */

/** Where the slow rise levels off, 0..1 of the logo. */
export const WAIT_LEVEL = 0.85
/** Time constant of the rise: ~63% of WAIT_LEVEL after this long. */
export const RISE_TAU_MS = 6000
/** Final surge to full once loading is done. */
export const SURGE_MS = 650
/** Waves settling after the surge, before the reveal. */
export const SETTLE_MS = 450
/** Logo grows and fades out, revealing what is behind. */
export const REVEAL_MS = 450
/** A loader mounting this soon after the last one hid continues its level. */
const HANDOVER_GRACE_MS = 1000

/** Level 0..1 after `elapsedMs` of waiting. */
export function fillLevel(elapsedMs: number): number {
  if (elapsedMs <= 0) return 0
  return WAIT_LEVEL * (1 - Math.exp(-elapsedMs / RISE_TAU_MS))
}

/** Water surface y in the logo's viewBox (543 high): 0 = top, 543 = bottom.
 *  Overshoots slightly at both ends so the waves are fully out of the logo
 *  when empty and fully cover it when full. */
export function levelToY(level: number): number {
  const clamped = Math.min(1, Math.max(0, level))
  return 560 - clamped * 590
}

let startedAt: number | null = null
let active = 0
let lastHidden = 0

/** A loader started waiting. Returns when this page's fill began. */
export function beginFill(now: number): number {
  if (active === 0 && (startedAt === null || now - lastHidden > HANDOVER_GRACE_MS)) {
    startedAt = now
  }
  active += 1
  return startedAt as number
}

/** A loader stopped waiting (surged or unmounted). */
export function endFill(now: number): void {
  active = Math.max(0, active - 1)
  if (active === 0) lastHidden = now
}

/** Tests only. */
export function _resetFillClock(): void {
  startedAt = null
  active = 0
  lastHidden = 0
}
