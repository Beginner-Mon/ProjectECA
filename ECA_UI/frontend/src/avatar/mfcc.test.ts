import { describe, expect, it } from 'vitest'
import { MFCC_COEFFS, createFeatureExtractor } from './mfcc'
import type { Viseme } from './AvatarProfile'

/**
 * Synthetic-signal tests for the MFCC front end. No files are read;
 * every signal below is generated deterministically.
 *
 * A fake vowel is a harmonic sum of F0 up to 5 kHz whose harmonic amplitude
 * at frequency f follows two formant bumps:
 *   amp(f) = Σ 1 / (1 + ((f − Fi) / 120)²)
 */

const FRAME_SIZE = 1024
const SR_48K = 48000
const SR_44K = 44100

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

function synthVowel(f0: number, sampleRate: number, f1: number, f2: number): Float32Array {
  const pcm = new Float32Array(FRAME_SIZE)
  const maxHarm = Math.floor(5000 / f0)
  for (let n = 0; n < FRAME_SIZE; n++) {
    const t = n / sampleRate
    let s = 0
    for (let h = 1; h <= maxHarm; h++) {
      const f = h * f0
      const amp = 1 / (1 + ((f - f1) / 120) ** 2) + 1 / (1 + ((f - f2) / 120) ** 2)
      s += amp * Math.sin(2 * Math.PI * f * t)
    }
    pcm[n] = s
  }
  return pcm
}

function mfccOf(pcm: Float32Array, sampleRate: number): number[] {
  const ex = createFeatureExtractor(sampleRate, FRAME_SIZE)
  const out = new Float32Array(MFCC_COEFFS)
  ex.extract(pcm, out)
  return Array.from(out)
}

interface TestTemplate {
  viseme: Viseme
  word: string
  mfcc: number[]
}

/** Templates built from fake vowels at F0 = 220 Hz. */
function buildTemplates(sampleRate: number): TestTemplate[] {
  return FAKE_VOWELS.map((v) => ({
    viseme: v.viseme,
    word: `fake-${v.viseme}`,
    mfcc: mfccOf(synthVowel(220, sampleRate, v.f1, v.f2), sampleRate),
  }))
}

/** Euclidean distance on the 12 kept coefficients. */
function euclid(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let sum = 0
  for (let i = 0; i < MFCC_COEFFS; i++) {
    const d = a[i] - b[i]
    sum += d * d
  }
  return Math.sqrt(sum)
}

function nearestViseme(mfcc: ArrayLike<number>, templates: readonly TestTemplate[]): Viseme {
  let best: Viseme = templates[0].viseme
  let bestDist = Number.POSITIVE_INFINITY
  for (const t of templates) {
    const d = euclid(mfcc, t.mfcc)
    if (d < bestDist) {
      bestDist = d
      best = t.viseme
    }
  }
  return best
}

describe('mfcc', () => {
  it('gives large low-band power to a 300 Hz sine and near-zero to a 3000 Hz sine', () => {
    const ex = createFeatureExtractor(SR_48K, FRAME_SIZE)
    const out = new Float32Array(MFCC_COEFFS)
    const low = new Float32Array(FRAME_SIZE)
    const high = new Float32Array(FRAME_SIZE)
    for (let n = 0; n < FRAME_SIZE; n++) {
      low[n] = Math.sin((2 * Math.PI * 300 * n) / SR_48K)
      high[n] = Math.sin((2 * Math.PI * 3000 * n) / SR_48K)
    }
    const pLow = ex.extract(low, out)
    const pHigh = ex.extract(high, out)
    expect(pLow).toBeGreaterThan(0)
    expect(pHigh / pLow).toBeLessThan(1e-3)
  })

  it('is invariant to loudness (x vs 0.1·x differ by < 1e-3 per coefficient)', () => {
    const pcm = synthVowel(220, SR_48K, 850, 1220)
    const quiet = new Float32Array(FRAME_SIZE)
    for (let n = 0; n < FRAME_SIZE; n++) {
      quiet[n] = 0.1 * pcm[n]
    }
    const loud = mfccOf(pcm, SR_48K)
    const soft = mfccOf(quiet, SR_48K)
    for (let i = 0; i < MFCC_COEFFS; i++) {
      expect(Math.abs(loud[i] - soft[i])).toBeLessThan(1e-3)
    }
  })

  it('matches the same vowel across F0 (templates at 220 Hz, queries at 180 and 260 Hz)', () => {
    const templates = buildTemplates(SR_48K)
    for (const v of FAKE_VOWELS) {
      for (const f0 of [180, 260]) {
        const mfcc = mfccOf(synthVowel(f0, SR_48K, v.f1, v.f2), SR_48K)
        expect(nearestViseme(mfcc, templates)).toBe(v.viseme)
      }
    }
  })

  it('matches across sample rates (templates at 48000 Hz, queries synthesised at 44100 Hz)', () => {
    const templates = buildTemplates(SR_48K)
    for (const v of FAKE_VOWELS) {
      for (const f0 of [180, 220, 260]) {
        const mfcc = mfccOf(synthVowel(f0, SR_44K, v.f1, v.f2), SR_44K)
        expect(nearestViseme(mfcc, templates)).toBe(v.viseme)
      }
    }
  })

  it('keeps white noise farther from every template than any vowel is from its own', () => {
    const templates = buildTemplates(SR_48K)
    let vowelMax = 0
    for (const v of FAKE_VOWELS) {
      for (const f0 of [180, 220, 260]) {
        const mfcc = mfccOf(synthVowel(f0, SR_48K, v.f1, v.f2), SR_48K)
        const own = templates.find((t) => t.viseme === v.viseme)
        if (!own) {
          throw new Error(`missing template for ${v.viseme}`)
        }
        const d = euclid(mfcc, own.mfcc)
        if (d > vowelMax) {
          vowelMax = d
        }
      }
    }
    // Deterministic white noise (LCG, fixed seed); mean MFCC over 8 frames.
    let state = 0x12345678
    const mean = new Array<number>(MFCC_COEFFS).fill(0)
    const frames = 8
    for (let f = 0; f < frames; f++) {
      const pcm = new Float32Array(FRAME_SIZE)
      for (let n = 0; n < FRAME_SIZE; n++) {
        state = (Math.imul(state, 1664525) + 1013904223) >>> 0
        pcm[n] = (state / 0x100000000) * 2 - 1
      }
      const mfcc = mfccOf(pcm, SR_48K)
      for (let i = 0; i < MFCC_COEFFS; i++) {
        mean[i] += mfcc[i] / frames
      }
    }
    let noiseMin = Number.POSITIVE_INFINITY
    for (const t of templates) {
      const d = euclid(mean, t.mfcc)
      if (d < noiseMin) {
        noiseMin = d
      }
    }
    expect(noiseMin).toBeGreaterThan(vowelMax)
  })
})
