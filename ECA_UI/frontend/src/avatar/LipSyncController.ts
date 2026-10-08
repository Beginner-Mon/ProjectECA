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

/**
 * Lip sync: Mode 1 amplitude (production) + DEV-only vowel visemes (Phase B).
 *
 * Mode 1 drives the mouth from RMS (fast attack, slower release) onto aa
 * with a touch of ou. VieNeu-TTS-GGUF exports no phoneme timestamps, so
 * Mode 1 is what production always runs.
 *
 * Mode 2 (vowel) only exists when import.meta.env.DEV: each tick also feeds
 * the analyser's time-domain data through the Phase-A vowel classifier
 * (12 MFCCs vs the anne_en templates) and eases five viseme shares toward
 * the winner. It reads audio already flowing through the render loop — it
 * never stands between decode and chunk scheduling (~30 µs/frame in Phase A).
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
const SHAPE_PER_SEC = 20
const VISEMES: readonly Viseme[] = ['A', 'I', 'U', 'E', 'O']
const VISEME_GAIN = [1, 1, 1, 1, 1]

export class LipSyncController implements ExpressionContributor {
  private readonly aaChannel: string
  private readonly ouChannel: string
  private readonly channels: readonly string[]

  private analyser: AnalyserNode | null = null
  private buffer: Float32Array<ArrayBuffer> = new Float32Array(0)
  private active = false
  private weight = 0

  private readonly templates: VowelTemplateSet | null
  private vowelModeOn = false
  private extractor: FeatureExtractor | null = null
  private extractorKey = ''
  private readonly mfcc = new Float32Array(MFCC_COEFFS)
  private readonly shares = [1, 0, 0, 0, 0]
  private targetIndex = 0

  constructor(profile: AvatarProfile, templates: VowelTemplateSet | null = anneEn) {
    this.aaChannel = profile.visemes.A
    this.ouChannel = profile.visemes.U
    this.channels = VISEMES.map((v) => profile.visemes[v])
    this.templates = templates
    this.vowelModeOn = import.meta.env.DEV && templates !== null
  }

  /** Only takes effect when import.meta.env.DEV; production stays Mode 1. */
  setVowelMode(on: boolean): void {
    this.vowelModeOn = on && import.meta.env.DEV && this.templates !== null
  }

  get vowelMode(): boolean {
    return this.vowelModeOn
  }

  /** Selected viseme, or '-' when off, idle, or the mouth is closed. */
  debugViseme(): Viseme | '-' {
    if (!this.vowelActive || !this.active || this.weight <= MIN_VISIBLE) {
      return '-'
    }
    return VISEMES[this.targetIndex]
  }

  /** Vowel path usable right now: mode on, templates loaded, frame sizes match. */
  private get vowelActive(): boolean {
    return (
      this.vowelModeOn &&
      this.templates !== null &&
      this.extractor !== null &&
      this.analyser !== null &&
      this.analyser.fftSize === this.templates.frameSize
    )
  }

  /** Begin driving the mouth from this analyser (created by the audio glue). */
  start(analyser: AnalyserNode): void {
    this.analyser = analyser
    this.buffer = new Float32Array(analyser.fftSize)
    this.active = true
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

    // Asymmetric, frame-rate-independent smoothing: snap open, ease closed.
    const rate = target > this.weight ? ATTACK_PER_SEC : RELEASE_PER_SEC
    const k = 1 - Math.exp(-rate * delta)
    this.weight += (target - this.weight) * k

    // Vowel classification (DEV only): below the audibility floor the old
    // target is kept, and a rejected frame never moves it either.
    const templates = this.templates
    const extractor = this.extractor
    if (
      this.vowelModeOn &&
      templates !== null &&
      extractor !== null &&
      this.analyser !== null &&
      this.analyser.fftSize === templates.frameSize &&
      target > VOWEL_MIN_TARGET
    ) {
      extractor.extract(this.buffer, this.mfcc)
      const hit = nearestTemplate(this.mfcc, templates.templates)
      if (hit.index >= 0 && hit.distance <= templates.rejectDistance) {
        const idx = VISEMES.indexOf(templates.templates[hit.index].viseme)
        if (idx >= 0) {
          this.targetIndex = idx
        }
      }
    }

    // Ease the five shares toward one-hot(targetIndex); the sum stays 1.
    const sk = 1 - Math.exp(-SHAPE_PER_SEC * delta)
    for (let i = 0; i < this.shares.length; i++) {
      const goal = i === this.targetIndex ? 1 : 0
      this.shares[i] += (goal - this.shares[i]) * sk
    }
  }

  contribute(frame: Map<string, number>): void {
    if (this.weight <= MIN_VISIBLE) return
    if (!this.vowelActive) {
      // Mode 1: open jaw (aa) with a touch of rounding (ou), nothing else.
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
  }
}

function clamp01(v: number): number {
  if (v < 0) return 0
  if (v > 1) return 1
  return v
}
