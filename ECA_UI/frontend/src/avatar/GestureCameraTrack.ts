/**
 * A number timed to a gesture's clip — drives the gesture's camera extras:
 *   - zoom   (GestureDef.cameraZoom):   rest 1; multiplies the face-lock distance
 *   - partner (GestureDef.partnerView): rest 0; how far the camera has moved
 *     into the partner's place (0 = normal face lock, 1 = partner's eyes)
 *
 * Owned by AvatarController next to the gesture's face track and started at the
 * same moment (when the clip starts), so key times line up with the motion.
 * Smoothstep between keys; outside a gesture it sits at its rest value; stop()
 * eases back to rest.
 */

export interface TrackKey {
  t: number
  value: number
}

const STOP_EASE_SEC = 0.35

export class GestureCameraTrack {
  private readonly rest: number
  private keys: TrackKey[] = []
  private elapsed = 0
  private active = false
  private stopFrom: number | null = null
  private stopElapsed = 0
  private current: number

  constructor(rest = 1) {
    this.rest = rest
    this.current = rest
  }

  get value(): number {
    return this.current
  }

  play(track: TrackKey[]): void {
    const sorted = [...track].sort((a, b) => a.t - b.t)
    this.stopFrom = null
    if (sorted.length < 2 || sorted[sorted.length - 1].t <= sorted[0].t) {
      this.active = false
      this.current = this.rest
      return
    }
    this.keys = sorted.map((k) => ({ t: k.t - sorted[0].t, value: k.value }))
    this.elapsed = 0
    this.active = true
    this.current = this.keys[0].value
  }

  /** Gesture interrupted: ease back to the rest value. */
  stop(): void {
    if (!this.active) return
    this.active = false
    this.stopFrom = this.current
    this.stopElapsed = 0
  }

  tick(delta: number): void {
    if (this.stopFrom !== null) {
      this.stopElapsed += delta
      const u = Math.min(1, this.stopElapsed / STOP_EASE_SEC)
      this.current = this.stopFrom + (this.rest - this.stopFrom) * smoothstep(u)
      if (u >= 1) {
        this.stopFrom = null
        this.current = this.rest
      }
      return
    }
    if (!this.active) return
    this.elapsed += delta
    const keys = this.keys
    if (this.elapsed >= keys[keys.length - 1].t) {
      this.active = false
      this.current = this.rest
      return
    }
    let i = 0
    while (i < keys.length - 2 && keys[i + 1].t <= this.elapsed) i++
    const a = keys[i]
    const b = keys[i + 1]
    const u = b.t > a.t ? smoothstep(Math.min(1, (this.elapsed - a.t) / (b.t - a.t))) : 1
    this.current = a.value + (b.value - a.value) * u
  }
}

function smoothstep(x: number): number {
  return x * x * (3 - 2 * x)
}
