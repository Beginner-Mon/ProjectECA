import type { CanonicalEmotion } from './AvatarProfile'
import type { ExpressionController } from './ExpressionController'
import type { EyeController } from './EyeController'

/**
 * Client-autonomous idle behavior (facial-animation-plan.md §4). Active only in
 * IDLE — the AvatarController gates it. Drives the expression + eye controllers
 * DIRECTLY (not through AvatarController.setEmotion) so it never refreshes the
 * engagement timer.
 *
 * Emotion wanderer alternates rest <-> one calm emotion so it always
 * passes through neutral (never jumps between two expressions). Gaze wanderer
 * nudges a look target; EyeController still lets a live mouse win.
 */
// Decided by Tri, 10/10/2026: idle shows only `relaxed`, at full intensity,
// 50-60 s apart. Replaces the earlier happy/relaxed mix at 0.15-0.4 every 4-9 s.
const IDLE_EMOTION: CanonicalEmotion = 'relaxed'
const EMOTION_INTENSITY = 1
const REST_GAP_SEC: [number, number] = [50, 60] // neutral, before an expression
const EMOTION_HOLD_SEC: [number, number] = [4, 9] // expression held before neutral
const EMOTION_TRANSITION_MS = 800
const GAZE_INTERVAL_SEC: [number, number] = [2, 5]
const GAZE_RANGE = 0.3

export class IdleBehaviorController {
  private readonly expression: ExpressionController
  private readonly eye: EyeController
  private readonly skipEmotions: Set<CanonicalEmotion>

  private emotionTimer = randomRange(REST_GAP_SEC)
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
    this.emotionTimer = randomRange(REST_GAP_SEC)
    this.gazeTimer = randomRange(GAZE_INTERVAL_SEC)
    this.atRest = true
  }

  tick(delta: number): void {
    // Emotion wanderer: alternate rest <-> calm emotion (always via neutral).
    this.emotionTimer -= delta
    if (this.emotionTimer <= 0) {
      if (this.atRest) {
        this.expression.setEmotion(this.idleEmotion(), EMOTION_INTENSITY, EMOTION_TRANSITION_MS)
        this.atRest = false
        this.emotionTimer = randomRange(EMOTION_HOLD_SEC)
      } else {
        this.expression.setEmotion('neutral', 1, EMOTION_TRANSITION_MS)
        this.atRest = true
        this.emotionTimer = randomRange(REST_GAP_SEC)
      }
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

  /** The idle emotion, or neutral if the profile lists it as binary (on/off morphs can't blend). */
  private idleEmotion(): CanonicalEmotion {
    return this.skipEmotions.has(IDLE_EMOTION) ? 'neutral' : IDLE_EMOTION
  }
}

function randomRange([min, max]: [number, number]): number {
  return min + Math.random() * (max - min)
}

function randomSigned(magnitude: number): number {
  return (Math.random() * 2 - 1) * magnitude
}
