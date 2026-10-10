import type { CanonicalEmotion } from './AvatarProfile'
import type { ExpressionController } from './ExpressionController'
import type { EyeController } from './EyeController'

/**
 * Client-autonomous idle behavior (facial-animation-plan.md §4). Active only in
 * IDLE — the AvatarController gates it. Drives the expression + eye controllers
 * DIRECTLY (not through AvatarController.setEmotion) so it never refreshes the
 * engagement timer.
 *
 * Emotion wanderer alternates rest <-> a weighted calm emotion so it always
 * passes through neutral (never jumps between two expressions). Gaze wanderer
 * nudges a look target; EyeController still lets a live mouse win.
 */
const EMOTION_INTERVAL_SEC: [number, number] = [4, 9]
const EMOTION_TRANSITION_MS = 800
const GAZE_INTERVAL_SEC: [number, number] = [2, 5]
const GAZE_RANGE = 0.3

// Product owner decision, 10/10/2026: idle expressions play at full intensity
// and lean relaxed over happy. This replaces the earlier low-intensity
// (0.15-0.4), happy-leaning (62/38) values.
const EMOTION_INTENSITY = 1

// Weighted calm emotions. Only calm expressions in idle.
const EMOTION_WEIGHTS: Array<{ emotion: CanonicalEmotion; weight: number }> = [
  { emotion: 'relaxed', weight: 0.62 },
  { emotion: 'happy', weight: 0.38 },
]

export class IdleBehaviorController {
  private readonly expression: ExpressionController
  private readonly eye: EyeController
  private readonly skipEmotions: Set<CanonicalEmotion>

  private emotionTimer = randomRange(EMOTION_INTERVAL_SEC)
  private gazeTimer = randomRange(GAZE_INTERVAL_SEC)
  private atRest = true

  constructor(
    expression: ExpressionController,
    eye: EyeController,
    binaryEmotions?: readonly CanonicalEmotion[],
  ) {
    this.expression = expression
    this.eye = eye
    this.skipEmotions = new Set(binaryEmotions ?? [])
  }

  /** Called when the avatar (re)enters IDLE — restart wander timers. */
  reset(): void {
    this.emotionTimer = randomRange(EMOTION_INTERVAL_SEC)
    this.gazeTimer = randomRange(GAZE_INTERVAL_SEC)
    this.atRest = true
  }

  tick(delta: number): void {
    // Emotion wanderer: alternate rest <-> calm emotion (always via neutral).
    this.emotionTimer -= delta
    if (this.emotionTimer <= 0) {
      if (this.atRest) {
        const emotion = this.pickWeighted()
        this.expression.setEmotion(emotion, EMOTION_INTENSITY, EMOTION_TRANSITION_MS)
        this.atRest = false
      } else {
        this.expression.setEmotion('neutral', 1, EMOTION_TRANSITION_MS)
        this.atRest = true
      }
      this.emotionTimer = randomRange(EMOTION_INTERVAL_SEC)
    }

    // Gaze wanderer: occasional saccade to a nearby point, sometimes back to center.
    this.gazeTimer -= delta
    if (this.gazeTimer <= 0) {
      if (Math.random() < 0.35) {
        this.eye.setWander(0, 0)
      } else {
        this.eye.setWander(randomSigned(GAZE_RANGE), randomSigned(GAZE_RANGE))
      }
      this.gazeTimer = randomRange(GAZE_INTERVAL_SEC)
    }
  }

  /** Pick a calm emotion, excluding binary (on/off) morphs that can't blend. */
  private pickWeighted(): CanonicalEmotion {
    const available = EMOTION_WEIGHTS.filter((w) => !this.skipEmotions.has(w.emotion))
    if (available.length === 0) return 'neutral'
    const total = available.reduce((s, w) => s + w.weight, 0)
    let r = Math.random() * total
    for (const { emotion, weight } of available) {
      r -= weight
      if (r <= 0) return emotion
    }
    return 'neutral'
  }
}

function randomRange([min, max]: [number, number]): number {
  return min + Math.random() * (max - min)
}

function randomSigned(magnitude: number): number {
  return (Math.random() * 2 - 1) * magnitude
}
