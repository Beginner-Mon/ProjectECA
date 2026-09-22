/**
 * Camera framing, driven by FSM state (plan §3.4).
 *
 * Owns only the *mode* (`head` close-up vs `hips` full-body) and the cooldown
 * timer. The orbit/follow maths stays in the R3F scene, which needs the live
 * camera and controls.
 *
 * Key rule: this timer changes the CAMERA only — it must never call
 * `transitionTo`. A system may have exactly one thing driving state. Plan v1.0
 * let a camera timer own an FSM state (`exercise_cooldown`) and that produced a
 * permanent deadlock (bug 🔴 #1).
 */

import { cameraModeOf, type CameraMode, type CharState } from './AnimationStates'

/** How long the wide framing is held after LEAVING a wide state. */
const COOLDOWN_MS = 3000

/** How long manual camera stays before auto-returning to head (2.5 min). */
const OVERRIDE_IDLE_MS = 150_000

export class CameraController {
  private state: CharState = 'idle'
  private mode: CameraMode = 'head'
  private timer: ReturnType<typeof setTimeout> | null = null
  private overrideTimer: ReturnType<typeof setTimeout> | null = null
  /**
   * Mode to go back to when a `face` lock ends. Set on entering the lock,
   * consumed on leaving it. `manual` is the case that matters: a user who had
   * taken the camera gets it back where the lock left it, rather than being
   * snapped to the auto `head` framing they had opted out of.
   */
  private resumeMode: CameraMode | null = null

  private readonly onModeChanged: (mode: CameraMode) => void

  constructor(onModeChanged: (mode: CameraMode) => void) {
    this.onModeChanged = onModeChanged
  }

  get cameraMode(): CameraMode {
    return this.mode
  }

  get isManual(): boolean {
    return this.mode === 'manual'
  }

  /** True while an FSM state owns the camera (see `CameraMode` `face`). */
  get isLocked(): boolean {
    return this.mode === 'face'
  }

  onStateChanged(next: CharState): void {
    const wasWide = cameraModeOf(this.state) === 'hips'
    const wasLocked = cameraModeOf(this.state) === 'face'
    this.state = next

    // The lock is decided BEFORE the manual check: it exists precisely to
    // override a user-held camera for the duration of one clip.
    if (cameraModeOf(next) === 'face') {
      if (!this.isLocked) this.resumeMode = this.mode
      this.clearTimer()
      this.clearOverrideTimer()
      this.set('face')
      return
    }
    if (wasLocked) {
      const resume = this.resumeMode ?? 'head'
      this.resumeMode = null
      if (resume === 'manual') {
        this.set('manual')
        this.restartOverrideTimer()
        return
      }
      // Anything else falls through to the ordinary rules below, which land
      // on `head` (or `hips` if the next state is wide).
    }

    if (this.mode === 'manual') return
    this.clearTimer()

    const isWide = cameraModeOf(next) === 'hips'
    if (isWide) {
      this.set('hips')
      return
    }
    if (wasWide) {
      // Keep the wide shot a moment longer so the motion's last pose is visible,
      // then ease back to the face. The FSM has already moved on to `idle`.
      this.timer = setTimeout(() => {
        this.timer = null
        this.set('head')
      }, COOLDOWN_MS)
      return
    }
    this.set('head')
  }

  /** Manual free-camera triggered by user drag/zoom/pan. */
  notifyManualInteraction(): void {
    // Orbit input is disabled in the scene while locked, so this is only a
    // guard against a stray onEnd that was in flight when the lock began.
    if (this.isLocked) return
    if (this.mode !== 'manual') {
      this.clearTimer()
      this.set('manual')
    }
    this.restartOverrideTimer()
  }

  /** Preset switch from UI. Exits manual and cancels idle timer. */
  setMode(mode: CameraMode): void {
    // `face` is not a preset anyone picks; it is granted by the FSM and taken
    // back when the state ends. Refusing it here keeps the lock from being
    // entered without a resumeMode, or broken by the dev panel mid-clip.
    if (mode === 'face' || this.isLocked) return
    this.clearOverrideTimer()
    this.clearTimer()
    this.set(mode)
  }

  dispose(): void {
    this.clearTimer()
    this.clearOverrideTimer()
  }

  private set(mode: CameraMode): void {
    if (this.mode === mode) return
    this.mode = mode
    this.onModeChanged(mode)
  }

  private clearTimer(): void {
    if (this.timer === null) return
    clearTimeout(this.timer)
    this.timer = null
  }

  private clearOverrideTimer(): void {
    if (this.overrideTimer === null) return
    clearTimeout(this.overrideTimer)
    this.overrideTimer = null
  }

  private restartOverrideTimer(): void {
    this.clearOverrideTimer()
    this.overrideTimer = setTimeout(() => {
      this.overrideTimer = null
      this.set('head')
    }, OVERRIDE_IDLE_MS)
  }
}
