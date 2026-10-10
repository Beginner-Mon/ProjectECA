import type { ExpressionContributor } from './ExpressionMixer'
import type { AvatarProfile, Viseme } from './AvatarProfile'
import {
  MFCC_COEFFS,
  createFeatureExtractor,
  type FeatureExtractor,
} from './mfcc'
import {
  articulationFeatures,
  shapeWeights,
  sharedVoiceRange,
} from './mouthShape'

/**
 * Lip sync: general mouth shapes by default, amplitude as fallback.
 *
 * - general (default in every build): each tick feeds the analyser's
 *   time-domain data through the MFCC front end plus the
 *   language-independent articulation mapping (openness/brightness
 *   normalised against the voice being heard, blended over five fixed
 *   anchors). Five shares ease toward the blend. It reads audio already
 *   flowing through the render loop — it never stands between decode and
 *   chunk scheduling (~30 µs/frame measured in Phase A).
 * - amplitude: mouth from RMS (fast attack 40/s, release 12/s) onto aa with
 *   a touch of ou. The fallback when the frame size mismatches, and what
 *   production ran before.
 *
 * The learned voice range persists across page loads (localStorage) so the
 * first seconds after a reload don't start from the built-in defaults.
 *
 * The three vowel timings live in module-level `vowelTuning` (editable from
 * the dev panel, DEV-only like the mode switch) so they survive controller
 * recreation on model switch.
 *
 * Owns the mouth viseme channels. Runs AFTER the emotion contributor in the
 * mixer so it OVERRIDES the mouth while audio plays; when silent it decays to 0
 * and emotion owns the mouth again (§5).
 *
 * The audio clock is the source of truth; wall-clock is not used.
 */
const RMS_GAIN = 5
const ATTACK_PER_SEC = 40
const RELEASE_PER_SEC = 12
const MIN_VISIBLE = 0.01
const VOWEL_MIN_TARGET = 0.08
const VOWEL_FRAME_SIZE = 1024
const VISEMES: readonly Viseme[] = ['A', 'I', 'U', 'E', 'O']
const VISEME_GAIN = [1, 1, 1, 1, 1]

const VOICE_RANGE_KEY = 'eca-lipsync-voice-range'

export type LipSyncMode = 'amplitude' | 'general'

export interface LipSyncTuning {
  attackPerSec: number
  releasePerSec: number
  shapePerSec: number
}

export const DEFAULT_VOWEL_TUNING: LipSyncTuning = {
  attackPerSec: 16,
  releasePerSec: 6,
  shapePerSec: 12,
}

const vowelTuning: LipSyncTuning = { ...DEFAULT_VOWEL_TUNING }

try {
  if (typeof localStorage !== 'undefined') {
    const raw = localStorage.getItem(VOICE_RANGE_KEY)
    if (raw) {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        sharedVoiceRange.seed(parsed)
      }
    }
  }
} catch {
  // Storage unavailable or blocked — start from the built-in defaults.
}

export class LipSyncController implements ExpressionContributor {
  private readonly aaChannel: string
  private readonly ouChannel: string
  private readonly channels: readonly string[]

  private analyser: AnalyserNode | null = null
  private buffer: Float32Array<ArrayBuffer> = new Float32Array(0)
  private active = false
  private weight = 0

  private modeValue: LipSyncMode = 'general'
  private vowelRun = false
  private extractor: FeatureExtractor | null = null
  private extractorKey = ''
  private readonly mfcc = new Float32Array(MFCC_COEFFS)
  private readonly feat = new Float32Array(2)
  private readonly norm = new Float32Array(2)
  private readonly goal = new Float32Array([1, 0, 0, 0, 0])
  private readonly shares = [1, 0, 0, 0, 0]

  constructor(profile: AvatarProfile) {
    this.aaChannel = profile.visemes.A
    this.ouChannel = profile.visemes.U
    this.channels = VISEMES.map((v) => profile.visemes[v])
  }

  /** DEV-only lip-sync mode switch; production always runs the default. */
  setMode(mode: LipSyncMode): void {
    if (!import.meta.env.DEV) {
      return
    }
    this.modeValue = mode
  }

  get mode(): LipSyncMode {
    return this.modeValue
  }

  get tuning(): Readonly<LipSyncTuning> {
    return vowelTuning
  }

  /** DEV-only vowel timing patch. Clamped: attack 4..60, release 2..30, shape 4..40. */
  setTuning(patch: Partial<LipSyncTuning>): void {
    if (!import.meta.env.DEV) {
      return
    }
    if (patch.attackPerSec !== undefined) {
      vowelTuning.attackPerSec = clamp(patch.attackPerSec, 4, 60)
    }
    if (patch.releasePerSec !== undefined) {
      vowelTuning.releasePerSec = clamp(patch.releasePerSec, 2, 30)
    }
    if (patch.shapePerSec !== undefined) {
      vowelTuning.shapePerSec = clamp(patch.shapePerSec, 4, 40)
    }
  }

  /** Selected viseme, or '-' when off, idle, or the mouth is closed. */
  debugViseme(): Viseme | '-' {
    if (!this.vowelActive || !this.active || this.weight <= MIN_VISIBLE) {
      return '-'
    }
    let best = 0
    for (let i = 1; i < this.shares.length; i++) {
      if (this.shares[i] > this.shares[best]) {
        best = i
      }
    }
    return VISEMES[best]
  }

  /**
   * Viseme path for this play: general mode with a matching frame size.
   * Stays true through the post-stop tail so the closing mouth keeps its
   * shape instead of flashing amplitude mode.
   */
  private get vowelActive(): boolean {
    return this.vowelRun && this.modeValue === 'general'
  }

  /** Begin driving the mouth from this analyser (created by the audio glue). */
  start(analyser: AnalyserNode): void {
    this.analyser = analyser
    this.buffer = new Float32Array(analyser.fftSize)
    this.active = true
    this.vowelRun = analyser.fftSize === VOWEL_FRAME_SIZE
    const key = `${analyser.context.sampleRate}:${analyser.fftSize}`
    if (key !== this.extractorKey) {
      this.extractorKey = key
      this.extractor = createFeatureExtractor(analyser.context.sampleRate, analyser.fftSize)
    }
  }

  /** Stop reading; the mouth decays closed over the release time. */
  stop(): void {
    const ranGeneral = this.modeValue === 'general' && this.vowelRun
    this.active = false
    this.analyser = null
    if (ranGeneral) {
      try {
        if (typeof localStorage !== 'undefined') {
          const snap = sharedVoiceRange.snapshot()
          if (snap !== null) {
            localStorage.setItem(VOICE_RANGE_KEY, JSON.stringify(snap))
          }
        }
      } catch {
        // Storage unavailable or blocked — the range simply stays in memory.
      }
    }
  }

  get isPlaying(): boolean {
    return this.active
  }

  tick(delta: number): void {
    let target = 0
    if (this.active && this.analyser) {
      this.analyser.getFloatTimeDomainData(this.buffer)
      let sumSq = 0
      for (let i = 0; i < this.buffer.length; i++) {
        const v = this.buffer[i]
        sumSq += v * v
      }
      const rms = Math.sqrt(sumSq / this.buffer.length)
      target = clamp01(rms * RMS_GAIN)
    }

    // Asymmetric, frame-rate-independent smoothing. General plays use the dev
    // tuning (softer, no full close between syllables); everything else —
    // including amplitude mode — keeps 40/12 for comparison.
    const attack = this.vowelActive ? vowelTuning.attackPerSec : ATTACK_PER_SEC
    const release = this.vowelActive ? vowelTuning.releasePerSec : RELEASE_PER_SEC
    const rate = target > this.weight ? attack : release
    const k = 1 - Math.exp(-rate * delta)
    this.weight += (target - this.weight) * k

    // General mode (DEV and production alike): below the audibility floor the
    // old goal is kept. The voice range learns continuously from voiced frames.
    const extractor = this.extractor
    if (
      this.modeValue === 'general' &&
      this.vowelRun &&
      extractor !== null &&
      this.analyser !== null &&
      target > VOWEL_MIN_TARGET
    ) {
      extractor.extract(this.buffer, this.mfcc)
      articulationFeatures(this.mfcc, this.feat)
      sharedVoiceRange.update(this.feat)
      sharedVoiceRange.normalize(this.feat, this.norm)
      shapeWeights(this.norm[0], this.norm[1], this.goal)
    }

    // Ease the five shares toward the goal; the sum stays 1.
    const sk = 1 - Math.exp(-vowelTuning.shapePerSec * delta)
    for (let i = 0; i < this.shares.length; i++) {
      this.shares[i] += (this.goal[i] - this.shares[i]) * sk
    }
  }

  contribute(frame: Map<string, number>): void {
    if (this.weight <= MIN_VISIBLE) return
    if (!this.vowelActive) {
      // Amplitude mode: open jaw (aa) with a touch of rounding (ou), nothing else.
      frame.set(this.aaChannel, this.weight)
      frame.set(this.ouChannel, this.weight * 0.35)
      return
    }
    for (let i = 0; i < this.shares.length; i++) {
      frame.set(this.channels[i], this.weight * this.shares[i] * VISEME_GAIN[i])
    }
  }

  /** Debug read (verification only). */
  debugWeight(): number {
    return Number(this.weight.toFixed(3))
  }

  detach(): void {
    this.stop()
    this.weight = 0
    for (let i = 0; i < this.shares.length; i++) {
      this.shares[i] = i === 0 ? 1 : 0
    }
    for (let i = 0; i < this.goal.length; i++) {
      this.goal[i] = i === 0 ? 1 : 0
    }
  }
}

function clamp01(v: number): number {
  if (v < 0) return 0
  if (v > 1) return 1
  return v
}

function clamp(v: number, lo: number, hi: number): number {
  if (v < lo) return lo
  if (v > hi) return hi
  return v
}
