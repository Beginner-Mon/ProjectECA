import type { ExpressionContributor } from './ExpressionMixer'
import type { AvatarProfile, FaceKey, FaceToken, Viseme } from './AvatarProfile'

/**
 * Plays a gesture's facial track (GestureDef.face) in step with its clip —
 * e.g. the kiss: smile → lips purse and eyes close as the hand reaches the
 * face → smile again.
 *
 * Runs LAST in the mixer (facial-animation-plan.md §5): while active it owns
 * every channel its track names PLUS the mouth visemes and the blink channel,
 * so lip-sync cannot hold the jaw open through a pucker and auto-blink cannot
 * pop the eyes open mid-kiss. Everything else (other emotions, gaze) is left to
 * the contributors before it.
 *
 * Its output is blended over what the earlier contributors wrote with a short
 * envelope, so it fades in from the current face and hands the face back
 * smoothly — no snap at either end, and none when stopped early.
 */

const FADE_IN_SEC = 0.15
const FADE_OUT_SEC = 0.25
const VISEMES: readonly Viseme[] = ['A', 'I', 'U', 'E', 'O']

/** One keyframe in the profile's vocabulary → weights on this model's channels. */
export function resolveFaceKey(profile: AvatarProfile, face: FaceKey['face']): Map<string, number> {
  const out = new Map<string, number>()
  const add = (channel: string, w: number) => out.set(channel, (out.get(channel) ?? 0) + w)
  for (const [token, weight] of Object.entries(face) as Array<[FaceToken, number | undefined]>) {
    if (!weight) continue
    if ((VISEMES as readonly string[]).includes(token)) {
      add(profile.visemes[token as Viseme], weight)
    } else if (token === 'blink') {
      add(profile.blinkChannel, weight)
    } else if (token === 'blinkLeft' || token === 'blinkRight') {
      add(token, weight)
    } else {
      const recipe = profile.recipes[token as keyof AvatarProfile['recipes']]
      if (!recipe) continue
      for (const [channel, w] of Object.entries(recipe)) add(channel, w * weight)
    }
  }
  return out
}

/** Every channel any of this profile's face tracks can drive. The expression
 *  adapter only writes channels it manages, so these must be added to that set. */
export function faceTrackChannels(profile: AvatarProfile): Set<string> {
  const channels = new Set<string>()
  for (const gesture of Object.values(profile.gestures ?? {})) {
    for (const key of gesture.face ?? []) {
      for (const channel of resolveFaceKey(profile, key.face).keys()) channels.add(channel)
    }
  }
  return channels
}

interface ResolvedKey {
  t: number
  weights: Map<string, number>
}

export class GestureFaceController implements ExpressionContributor {
  private readonly profile: AvatarProfile
  /** Mouth + eye channels the track takes over while it plays, even at weight 0. */
  private readonly alwaysOwned: string[]

  private keys: ResolvedKey[] = []
  private owned: string[] = []
  private elapsed = 0
  private duration = 0
  private active = false
  // Reused per frame: the track's value at `elapsed`.
  private readonly sample = new Map<string, number>()

  constructor(profile: AvatarProfile) {
    this.profile = profile
    this.alwaysOwned = [...new Set([...Object.values(profile.visemes), profile.blinkChannel])]
  }

  get isPlaying(): boolean {
    return this.active
  }

  /** Start a track from its first key. A track needs two keys to span time. */
  play(track: FaceKey[]): void {
    const sorted = [...track].sort((a, b) => a.t - b.t)
    if (sorted.length < 2 || sorted[sorted.length - 1].t <= sorted[0].t) {
      this.active = false
      return
    }
    this.keys = sorted.map((k) => ({ t: k.t - sorted[0].t, weights: resolveFaceKey(this.profile, k.face) }))
    const owned = new Set(this.alwaysOwned)
    for (const k of this.keys) for (const channel of k.weights.keys()) owned.add(channel)
    this.owned = [...owned]
    this.duration = this.keys[this.keys.length - 1].t
    this.elapsed = 0
    this.active = true
  }

  /** Gesture interrupted: fade out from wherever the track is, quickly. */
  stop(): void {
    if (!this.active) return
    this.duration = Math.min(this.duration, this.elapsed + FADE_OUT_SEC)
  }

  tick(delta: number): void {
    if (!this.active) return
    this.elapsed += delta
    if (this.elapsed >= this.duration) this.active = false
  }

  contribute(frame: Map<string, number>): void {
    if (!this.active) return
    const envelope = clamp01(Math.min(this.elapsed / FADE_IN_SEC, (this.duration - this.elapsed) / FADE_OUT_SEC))
    this.sampleAt(this.elapsed)
    for (const channel of this.owned) {
      const under = frame.get(channel) ?? 0
      const target = this.sample.get(channel) ?? 0
      frame.set(channel, under + (target - under) * envelope)
    }
  }

  private sampleAt(t: number): void {
    this.sample.clear()
    const keys = this.keys
    let i = 0
    while (i < keys.length - 2 && keys[i + 1].t <= t) i++
    const a = keys[i]
    const b = keys[i + 1]
    const span = b.t - a.t
    const u = span > 0 ? smoothstep(clamp01((t - a.t) / span)) : 1
    for (const channel of this.owned) {
      const wa = a.weights.get(channel) ?? 0
      const wb = b.weights.get(channel) ?? 0
      const w = wa + (wb - wa) * u
      if (w !== 0) this.sample.set(channel, w)
    }
  }
}

function smoothstep(x: number): number {
  return x * x * (3 - 2 * x)
}

function clamp01(v: number): number {
  if (v < 0) return 0
  if (v > 1) return 1
  return v
}
