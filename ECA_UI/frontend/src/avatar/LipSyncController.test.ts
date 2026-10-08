import { describe, expect, it } from 'vitest'
import { DEFAULT_VOWEL_TUNING, LipSyncController } from './LipSyncController'
import { sharedVoiceRange } from './mouthShape'
import { defaultProfile } from './profiles/default'
import {
  MFCC_COEFFS,
  createFeatureExtractor,
  type VowelTemplate,
  type VowelTemplateSet,
} from './vowelClassifier'
import type { Viseme } from './AvatarProfile'

/**
 * Lip-sync modes (Phase B, DEV-only). The fake analyser feeds synthetic
 * vowels — harmonic sums with two formant bumps, peak-normalised to 0.5 —
 * and the test template set is built from those same signals, so
 * classification is exact and the tests measure plumbing, not acoustics.
 * Tuning and the shared voice range are module-level, so every test resets
 * both to a known state.
 */

const FRAME_SIZE = 1024
const SAMPLE_RATE = 48000
const TICK = 1 / 60

interface FakeVowel {
  viseme: Viseme
  f1: number
  f2: number
}

const FAKE_VOWELS: readonly FakeVowel[] = [
  { viseme: 'A', f1: 850, f2: 1220 },
  { viseme: 'I', f1: 310, f2: 2790 },
  { viseme: 'U', f1: 370, f2: 950 },
  { viseme: 'E', f1: 610, f2: 2330 },
  { viseme: 'O', f1: 590, f2: 920 },
]

function synthVowel(f0: number, f1: number, f2: number): Float32Array {
  const pcm = new Float32Array(FRAME_SIZE)
  const maxHarm = Math.floor(5000 / f0)
  for (let n = 0; n < FRAME_SIZE; n++) {
    const t = n / SAMPLE_RATE
    let s = 0
    for (let h = 1; h <= maxHarm; h++) {
      const f = h * f0
      const amp = 1 / (1 + ((f - f1) / 120) ** 2) + 1 / (1 + ((f - f2) / 120) ** 2)
      s += amp * Math.sin(2 * Math.PI * f * t)
    }
    pcm[n] = s
  }
  let peak = 0
  for (let n = 0; n < FRAME_SIZE; n++) {
    const a = Math.abs(pcm[n])
    if (a > peak) {
      peak = a
    }
  }
  for (let n = 0; n < FRAME_SIZE; n++) {
    pcm[n] = (pcm[n] / peak) * 0.5
  }
  return pcm
}

function buildTestTemplates(): VowelTemplateSet {
  const ex = createFeatureExtractor(SAMPLE_RATE, FRAME_SIZE)
  const out = new Float32Array(MFCC_COEFFS)
  const templates: VowelTemplate[] = FAKE_VOWELS.map((v) => {
    ex.extract(synthVowel(220, v.f1, v.f2), out)
    return { viseme: v.viseme, word: `test-${v.viseme}`, mfcc: Array.from(out) }
  })
  return { version: 1, frameSize: FRAME_SIZE, templates, rejectDistance: 1e9 }
}

function vowelSignal(viseme: Viseme): Float32Array {
  const v = FAKE_VOWELS.find((x) => x.viseme === viseme)
  if (!v) {
    throw new Error(`unknown viseme ${viseme}`)
  }
  return synthVowel(220, v.f1, v.f2)
}

/** Analyser fake: each tick copies the holder's current signal into buf. */
function fakeAnalyser(holder: { current: Float32Array }): AnalyserNode {
  return {
    fftSize: FRAME_SIZE,
    context: { sampleRate: SAMPLE_RATE },
    getFloatTimeDomainData(buf: Float32Array): void {
      buf.set(holder.current.subarray(0, buf.length))
    },
  } as unknown as AnalyserNode
}

function tickTimes(controller: LipSyncController, n: number): void {
  for (let i = 0; i < n; i++) {
    controller.tick(TICK)
  }
}

/** Analyser fake: 6 voiced ticks then 4 silent, repeating (syllable-ish). */
function patternAnalyser(voiced: Float32Array): AnalyserNode {
  const silent = new Float32Array(FRAME_SIZE)
  let n = 0
  return {
    fftSize: FRAME_SIZE,
    context: { sampleRate: SAMPLE_RATE },
    getFloatTimeDomainData(buf: Float32Array): void {
      const phase = n % 10
      n += 1
      buf.set((phase < 6 ? voiced : silent).subarray(0, buf.length))
    },
  } as unknown as AnalyserNode
}

describe('LipSyncController vowel mode', () => {
  it('Mode 1 regression: exactly aa + ou at 0.35x', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setMode('amplitude')
    const holder = { current: vowelSignal('I') }
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 30)
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(2)
    const aa = frame.get('aa') ?? 0
    expect(aa).toBeGreaterThan(0)
    expect(frame.get('ou') ?? 0).toBeCloseTo(0.35 * aa, 12)
  })

  it('vowel I: debugViseme I, five channels, ih largest, sum <= 1', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    const holder = { current: vowelSignal('I') }
    controller.setMode('template')
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 30)
    expect(controller.debugViseme()).toBe('I')
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(5)
    const ih = frame.get('ih') ?? 0
    for (const ch of ['aa', 'ou', 'ee', 'oh']) {
      expect(ih).toBeGreaterThan(frame.get(ch) ?? 0)
    }
    let sum = 0
    for (const v of frame.values()) {
      sum += v
    }
    expect(sum).toBeLessThanOrEqual(1 + 1e-9)
  })

  it('switches to A: debugViseme A, aa largest', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    const holder = { current: vowelSignal('I') }
    controller.setMode('template')
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 30)
    holder.current = vowelSignal('A')
    tickTimes(controller, 30)
    expect(controller.debugViseme()).toBe('A')
    const frame = new Map<string, number>()
    controller.contribute(frame)
    const aa = frame.get('aa') ?? 0
    for (const ch of ['ih', 'ou', 'ee', 'oh']) {
      expect(aa).toBeGreaterThan(frame.get(ch) ?? 0)
    }
  })

  it('silence: no channels, debugViseme -', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    const holder = { current: new Float32Array(FRAME_SIZE) }
    controller.setMode('template')
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 60)
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(0)
    expect(controller.debugViseme()).toBe('-')
  })

  it('no templates: template falls back to amplitude, general still shapes', () => {
    const controller = new LipSyncController(defaultProfile, null)
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setMode('template')
    expect(controller.mode).toBe('template')
    const holder = { current: vowelSignal('I') }
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 30)
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(2)
    const aa = frame.get('aa') ?? 0
    expect(aa).toBeGreaterThan(0)
    expect(frame.get('ou') ?? 0).toBeCloseTo(0.35 * aa, 12)
    expect(controller.debugViseme()).toBe('-')

    controller.setMode('general')
    tickTimes(controller, 30)
    const frame2 = new Map<string, number>()
    controller.contribute(frame2)
    expect(frame2.size).toBe(5)
    expect(controller.debugViseme()).not.toBe('-')
  })

  it('flickering labels: debugViseme changes at most 5 times in 30 ticks', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    const sigA = vowelSignal('A')
    const sigI = vowelSignal('I')
    const holder = { current: sigA }
    controller.setMode('template')
    controller.start(fakeAnalyser(holder))
    const seen: string[] = []
    for (let i = 0; i < 30; i++) {
      holder.current = i % 2 === 0 ? sigA : sigI
      controller.tick(TICK)
      seen.push(controller.debugViseme())
    }
    let changes = 0
    for (let i = 1; i < seen.length; i++) {
      if (seen[i] !== seen[i - 1]) {
        changes++
      }
    }
    expect(changes).toBeLessThanOrEqual(5)
  })

  it('no full close between syllables: vowel trough/peak > 0.6, Mode 1 < 0.5', () => {
    const ratio = (vowel: boolean): number => {
      const controller = new LipSyncController(defaultProfile, buildTestTemplates())
      controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
      controller.setMode(vowel ? 'template' : 'amplitude')
      controller.start(patternAnalyser(vowelSignal('A')))
      const weights: number[] = []
      for (let i = 0; i < 120; i++) {
        controller.tick(TICK)
        weights.push(controller.debugWeight())
      }
      const tail = weights.slice(60)
      return Math.min(...tail) / Math.max(...tail)
    }
    expect(ratio(true)).toBeGreaterThan(0.6)
    expect(ratio(false)).toBeLessThan(0.5)
  })

  it('tail after stop: still five channels, not Mode-1 aa', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    const holder = { current: vowelSignal('I') }
    controller.setMode('template')
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 30)
    controller.stop()
    controller.tick(TICK)
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(5)
    const w = controller.debugWeight()
    expect(frame.get('ih') ?? 0).toBeGreaterThan(0.5 * w)
    expect(frame.get('aa') ?? 0).toBeLessThan(0.5 * w)
    expect(controller.debugViseme()).toBe('-')
  })

  it('clamps tuning: attack 999 -> 60, dwell -5 -> 0', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setMode('template')
    controller.setTuning({ attackPerSec: 999, minDwellMs: -5 })
    expect(controller.tuning.attackPerSec).toBe(60)
    expect(controller.tuning.minDwellMs).toBe(0)
    expect(controller.tuning.releasePerSec).toBe(6)
  })

  it('general mode: five channels, sum <= 1, viseme shown', () => {
    const controller = new LipSyncController(defaultProfile, buildTestTemplates())
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setMode('general')
    const holder = { current: vowelSignal('I') }
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 30)
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(5)
    let sum = 0
    for (const v of frame.values()) {
      sum += v
    }
    expect(sum).toBeLessThanOrEqual(1 + 1e-9)
    expect(controller.debugViseme()).not.toBe('-')
  })
})
