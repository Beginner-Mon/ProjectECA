/**
 * Where the eyes go while the avatar is thinking (10-10).
 *
 * People thinking look UP and to one SIDE, hold it for a moment with small
 * involuntary eye movements, glance back now and then, and change sides. That
 * pattern is read instantly as "she's thinking"; fixed, centred eyes on a still
 * body read as a frozen app. HeadController follows the eyes, so the head
 * tilts with them a little.
 *
 * Drives EyeController's override (wins over the mouse) in normalized [-1..1]
 * coordinates, y up. Pure timing logic; `rand` is injectable for tests.
 */

export interface GazeTarget {
  setOverride(nx: number, ny: number): void
}

/** Looking away: up and to the side. */
const AWAY_X = 0.45
const AWAY_Y = 0.55
const AWAY_JITTER = 0.12
/** Holding a glance, seconds. */
const AWAY_HOLD: [number, number] = [1.6, 3.2]
/** Glance back toward the viewer, seconds. */
const BACK_HOLD: [number, number] = [0.6, 1.1]
const BACK_CHANCE = 0.3
const SWITCH_SIDE_CHANCE = 0.4
/** Micro-saccades while holding a glance. */
const SACCADE_EVERY: [number, number] = [0.35, 0.8]
const SACCADE_SIZE = 0.05

export class ThinkingGaze {
  private readonly eye: GazeTarget
  private readonly rand: () => number
  private side = 1
  private phase: 'away' | 'back' = 'away'
  private phaseLeft = 0
  private saccadeLeft = 0
  private baseX = 0
  private baseY = 0

  constructor(eye: GazeTarget, rand: () => number = Math.random) {
    this.eye = eye
    this.rand = rand
  }

  /** Begin a thinking spell: first glance goes up and to a random side. */
  start(): void {
    this.side = this.rand() < 0.5 ? -1 : 1
    this.lookAway()
  }

  tick(delta: number): void {
    this.phaseLeft -= delta
    if (this.phaseLeft <= 0) {
      if (this.phase === 'away' && this.rand() < BACK_CHANCE) {
        this.phase = 'back'
        this.baseX = 0.05 * this.side
        this.baseY = 0.05
        this.phaseLeft = this.between(BACK_HOLD)
        this.saccadeLeft = Infinity // a glance back is steady
        this.eye.setOverride(this.baseX, this.baseY)
      } else {
        if (this.rand() < SWITCH_SIDE_CHANCE) this.side = -this.side
        this.lookAway()
      }
      return
    }
    this.saccadeLeft -= delta
    if (this.saccadeLeft <= 0) {
      this.eye.setOverride(
        this.baseX + (this.rand() * 2 - 1) * SACCADE_SIZE,
        this.baseY + (this.rand() * 2 - 1) * SACCADE_SIZE,
      )
      this.saccadeLeft = this.between(SACCADE_EVERY)
    }
  }

  private lookAway(): void {
    this.phase = 'away'
    this.baseX = this.side * (AWAY_X + (this.rand() * 2 - 1) * AWAY_JITTER)
    this.baseY = AWAY_Y + (this.rand() * 2 - 1) * AWAY_JITTER
    this.phaseLeft = this.between(AWAY_HOLD)
    this.saccadeLeft = this.between(SACCADE_EVERY)
    this.eye.setOverride(this.baseX, this.baseY)
  }

  private between([a, b]: [number, number]): number {
    return a + this.rand() * (b - a)
  }
}
