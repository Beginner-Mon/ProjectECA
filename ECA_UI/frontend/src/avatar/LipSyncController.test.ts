import { describe, expect, it } from 'vitest'
import { DEFAULT_VOWEL_TUNING, LipSyncController } from './LipSyncController'
import { sharedVoiceRange } from './mouthShape'
import { defaultProfile } from './profiles/default'
import type { Viseme } from './AvatarProfile'

/**
 * Lip-sync modes (Phase B-ship). The fake analyser feeds synthetic vowels —
 * harmonic sums with two formant bumps, peak-normalised to 0.5 — so the
 * tests measure plumbing, not acoustics. Tuning and the shared voice range
 * are module-level, so every test resets both to a known state.
 */

const FRAME_SIZE = 1024
const SAMPLE_RATE = 48000
const TICK = 1 / 60
const VOICE_RANGE_KEY = 'eca-lipsync-voice-range'

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

function vowelSignal(viseme: Viseme): Float32Array {
  const v = FAKE_VOWELS.find((x) => x.viseme === viseme)
  if (!v) {
    throw new Error(`unknown viseme ${viseme}`)
  }
  return synthVowel(220, v.f1, v.f2)
}

/** Analyser fake: each tick copies the holder's current signal into buf. */
function fakeAnalyser(
  holder: { current: Float32Array },
  fftSize = FRAME_SIZE,
  sampleRate = SAMPLE_RATE,
): AnalyserNode {
  return {
    fftSize,
    context: { sampleRate },
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

describe('LipSyncController modes', () => {
  it('defaults to general in a fresh controller', () => {
    const controller = new LipSyncController(defaultProfile)
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    expect(controller.mode).toBe('general')
  })

  it('amplitude: exactly aa + ou at 0.35x', () => {
    const controller = new LipSyncController(defaultProfile)
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

  it('general with a synthetic vowel: five channels, sum <= 1, viseme shown', () => {
    const controller = new LipSyncController(defaultProfile)
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

  it('wrong fftSize: runs like amplitude even in general mode', () => {
    const controller = new LipSyncController(defaultProfile)
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setMode('general')
    const holder = { current: vowelSignal('I') }
    controller.start(fakeAnalyser(holder, 2048))
    tickTimes(controller, 30)
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(2)
    const aa = frame.get('aa') ?? 0
    expect(aa).toBeGreaterThan(0)
    expect(frame.get('ou') ?? 0).toBeCloseTo(0.35 * aa, 12)
  })

  it('no full close between syllables: trough/peak > 0.6 in general, < 0.5 in amplitude', () => {
    const ratio = (general: boolean): number => {
      const controller = new LipSyncController(defaultProfile)
      controller.setTuning(DEFAULT_VOWEL_TUNING)
      sharedVoiceRange.reset()
      controller.setMode(general ? 'general' : 'amplitude')
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

  it('tail after stop in general: still five channels while audible', () => {
    const controller = new LipSyncController(defaultProfile)
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setMode('general')
    const holder = { current: vowelSignal('I') }
    controller.start(fakeAnalyser(holder))
    tickTimes(controller, 30)
    controller.stop()
    controller.tick(TICK)
    expect(controller.debugWeight()).toBeGreaterThan(0.01)
    const frame = new Map<string, number>()
    controller.contribute(frame)
    expect(frame.size).toBe(5)
    expect(controller.debugViseme()).toBe('-')
  })

  it('clamps tuning: attack 999 -> 60, shape -5 -> 4', () => {
    const controller = new LipSyncController(defaultProfile)
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setTuning({ attackPerSec: 999, shapePerSec: -5 })
    expect(controller.tuning.attackPerSec).toBe(60)
    expect(controller.tuning.shapePerSec).toBe(4)
    expect(controller.tuning.releasePerSec).toBe(6)
  })

  it('persists the learned voice range to localStorage on stop', () => {
    const controller = new LipSyncController(defaultProfile)
    controller.setTuning(DEFAULT_VOWEL_TUNING)
    sharedVoiceRange.reset()
    controller.setMode('general')
    const store = new Map<string, string>()
    const calls: Array<[string, string]> = []
    const fakeStorage = {
      getItem: (k: string): string | null => store.get(k) ?? null,
      setItem: (k: string, v: string): void => {
        store.set(k, v)
        calls.push([k, v])
      },
    }
    const holder = { current: vowelSignal('A') }
    const scope = globalThis as unknown as { localStorage?: unknown }
    const prev = scope.localStorage
    scope.localStorage = fakeStorage
    try {
      controller.start(fakeAnalyser(holder))
      tickTimes(controller, 700)
      controller.stop()
    } finally {
      if (prev === undefined) {
        delete scope.localStorage
      } else {
        scope.localStorage = prev
      }
    }
    const saved = calls.filter(([k]) => k === VOICE_RANGE_KEY)
    expect(saved.length).toBeGreaterThan(0)
    const parsed: unknown = JSON.parse(saved[saved.length - 1][1])
    expect(Array.isArray(parsed)).toBe(true)
    const nums = parsed as number[]
    expect(nums.length).toBe(4)
    for (const v of nums) {
      expect(Number.isFinite(v)).toBe(true)
    }
  })
})
