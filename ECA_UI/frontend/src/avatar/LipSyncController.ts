import type { ExpressionContributor } from './ExpressionMixer'
import type { AvatarProfile, Viseme } from './AvatarProfile'
import {
  MFCC_COEFFS,
  createFeatureExtractor,
  nearestTemplate,
  type FeatureExtractor,
  type VowelTemplateSet,
} from './vowelClassifier'
import { anneEn } from './vowelTemplates/anne_en'
import {
  articulationFeatures,
  shapeWeights,
  sharedVoiceRange,
} from './mouthShape'

/**
 * Lip sync: Mode 1 amplitude (production) + two DEV-only viseme modes (Phase B).
 *
 * - amplitude: mouth from RMS (fast attack 40/s, release 12/s) onto aa with a
 *   touch of ou. VieNeu-TTS-GGUF exports no phoneme timestamps, so this is
 *   what production always runs.
 * - template: Phase-A vowel classifier (12 MFCCs vs the anneEn templates).
 *   The raw label is noisy (~30% frames wrong), so the target only moves on
 *   a 5-frame majority vote with a minimum dwell, and five shares ease
 *   toward it. English templates; needs templates loaded.
 * - general: language-independent shape from articulation features
 *   (openness/brightness normalised against the voice being heard, blended
 *   over five fixed anchors). Continuous output: no voting, no dwell.
 *
 * Both viseme modes read audio already flowing through the render loop — they
 * never stand between decode and chunk scheduling (~30 µs/frame in Phase A).
 *
 * The four vowel timings live in module-level `vowelTuning` (editable from
 * the dev panel) so they survive controller recreation on model switch.
 * Mode amplitude always uses 40/12, for comparison.
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
const VOWEL_VOTE_FRAMES = 5
const VOWEL_FRAME_SIZE = 1024
const VISEMES: readonly Viseme[] = ['A', 'I', 'U', 'E', 'O']
const VISEME_GAIN = [1, 1, 1, 1, 1]

export type LipSyncMode = 'amplitude' | 'template' | 'general'

export interface LipSyncTuning {
  attackPerSec: number
  releasePerSec: number
  minDwellMs: number
  shapePerSec: number
}

export const DEFAULT_VOWEL_TUNING: LipSyncTuning = {
  attackPerSec: 16,
  releasePerSec: 6,
  minDwellMs: 120,
  shapePerSec: 12,
}

const vowelTuning: LipSyncTuning = { ...DEFAULT_VOWEL_TUNING }

export class LipSyncController implements ExpressionContributor {
  private readonly aaChannel: string
  private readonly ouChannel: string
  private readonly channels: readonly string[]

  private analyser: AnalyserNode | null = null
  private buffer: Float32Array<ArrayBuffer> = new Float32Array(0)
  private active = false
  private weight = 0

  private modeValue: LipSyncMode
  private readonly templates: VowelTemplateSet | null
  private vowelRun = false
  private extractor: FeatureExtractor | null = null
  private extractorKey = ''
  private readonly mfcc = new Float32Array(MFCC_COEFFS)
  private readonly feat = new Float32Array(2)
  private readonly norm = new Float32Array(2)
  private readonly goal = new Float32Array([1, 0, 0, 0, 0])
  private readonly shares = [1, 0, 0, 0, 0]
  private targetIndex = 0
  private readonly votes = new Array<number>(VOWEL_VOTE_FRAMES).fill(-1)
  private voteHead = 0
  private voteCount = 0
  private dwellMs = 1e9

  constructor(profile: AvatarProfile, templates: VowelTemplateSet | null = anneEn) {
    this.aaChannel = profile.visemes.A
    this.ouChannel = profile.visemes.U
    this.channels = VISEMES.map((v) => profile.visemes[v])
    this.templates = templates
    this.modeValue = import.meta.env.DEV ? 'general' : 'amplitude'
  }

  /** DEV-only lip-sync mode switch; production stays 'amplitude'. */
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

  /** DEV-only vowel timing patch. Clamped: attack 4..60, release 2..30, dwell 0..400, shape 4..40. */
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
    if (patch.minDwellMs !== undefined) {
      vowelTuning.minDwellMs = clamp(patch.minDwellMs, 0, 400)
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
    if (this.modeValue === 'general') {
      let best = 0
      for (let i = 1; i < this.shares.length; i++) {
        if (this.shares[i] > this.shares[best]) {
          best = i
        }
      }
      return VISEMES[best]
    }
    return VISEMES[this.targetIndex]
  }

  /**
   * Viseme path for this play: a viseme mode with a matching frame size
   * (template mode also needs templates). Stays true through the post-stop
   * tail so the closing mouth keeps its shape instead of flashing Mode 1.
   */
  private get vowelActive(): boolean {
    if (!this.vowelRun || this.modeValue === 'amplitude') {
      return false
    }
    if (this.modeValue === 'template') {
      return this.templates !== null
    }
    return true
  }

  /** Begin driving the mouth from this analyser (created by the audio glue). */
  start(analyser: AnalyserNode): void {
    this.analyser = analyser
    this.buffer = new Float32Array(analyser.fftSize)
    this.active = true
    this.vowelRun = analyser.fftSize === VOWEL_FRAME_SIZE
    this.voteHead = 0
    this.voteCount = 0
    this.votes.fill(-1)
    this.dwellMs = 1e9
    const key = `${analyser.context.sampleRate}:${analyser.fftSize}`
    if (key !== this.extractorKey) {
      this.extractorKey = key
      this.extractor = createFeatureExtractor(analyser.context.sampleRate, analyser.fftSize)
    }
  }

  /** Stop reading; the mouth decays closed over the release time. */
  stop(): void {
    this.active = false
    this.analyser = null
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

    // Asymmetric, frame-rate-independent smoothing. Viseme plays use the dev
    // tuning (softer, no full close between syllables); everything else —
    // including amplitude mode — keeps 40/12 for comparison.
    const attack = this.vowelActive ? vowelTuning.attackPerSec : ATTACK_PER_SEC
    const release = this.vowelActive ? vowelTuning.releasePerSec : RELEASE_PER_SEC
    const rate = target > this.weight ? attack : release
    const k = 1 - Math.exp(-rate * delta)
    this.weight += (target - this.weight) * k

    this.dwellMs += delta * 1000

    // Viseme target (DEV only): below the audibility floor the old goal is
    // kept. Template mode votes with a minimum dwell; general mode blends
    // continuously from the voice-normalised articulation features.
    const templates = this.templates
    const extractor = this.extractor
    if (
      this.modeValue !== 'amplitude' &&
      this.vowelRun &&
      extractor !== null &&
      this.analyser !== null &&
      target > VOWEL_MIN_TARGET
    ) {
      if (this.modeValue === 'template' && templates !== null) {
        extractor.extract(this.buffer, this.mfcc)
        const hit = nearestTemplate(this.mfcc, templates.templates)
        if (hit.index >= 0 && hit.distance <= templates.rejectDistance) {
          const idx = VISEMES.indexOf(templates.templates[hit.index].viseme)
          if (idx >= 0) {
            this.votes[this.voteHead] = idx
            this.voteHead = (this.voteHead + 1) % VOWEL_VOTE_FRAMES
            if (this.voteCount < VOWEL_VOTE_FRAMES) {
              this.voteCount++
            }
            let candidate = this.targetIndex
            let candidateVotes = 0
            for (let j = 0; j < this.voteCount; j++) {
              if (this.votes[j] === this.targetIndex) {
                candidateVotes++
              }
            }
            for (let i = 0; i < VISEMES.length; i++) {
              if (i === this.targetIndex) {
                continue
              }
              let c = 0
              for (let j = 0; j < this.voteCount; j++) {
                if (this.votes[j] === i) {
                  c++
                }
              }
              if (c > candidateVotes) {
                candidate = i
                candidateVotes = c
              }
            }
            if (candidate !== this.targetIndex && this.dwellMs >= vowelTuning.minDwellMs) {
              this.targetIndex = candidate
              this.dwellMs = 0
            }
          }
        }
        for (let i = 0; i < this.goal.length; i++) {
          this.goal[i] = i === this.targetIndex ? 1 : 0
        }
      } else if (this.modeValue === 'general') {
        extractor.extract(this.buffer, this.mfcc)
        articulationFeatures(this.mfcc, this.feat)
        sharedVoiceRange.update(this.feat)
        sharedVoiceRange.normalize(this.feat, this.norm)
        shapeWeights(this.norm[0], this.norm[1], this.goal)
      }
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
    this.targetIndex = 0
    for (let i = 0; i < this.shares.length; i++) {
      this.shares[i] = i === 0 ? 1 : 0
    }
    for (let i = 0; i < this.goal.length; i++) {
      this.goal[i] = i === 0 ? 1 : 0
    }
    this.voteHead = 0
    this.voteCount = 0
    this.votes.fill(-1)
    this.dwellMs = 1e9
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
