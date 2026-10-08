import { describe, expect, it } from 'vitest'
import {
  VISEMES,
  VoiceRange,
  articulationFeatures,
  shapeWeights,
} from './mouthShape'
import { MFCC_COEFFS, createFeatureExtractor } from './vowelClassifier'

/**
 * Language-independent mouth shape (Phase B3, DEV-only). Synthetic signals
 * only; no files are read.
 */

const FRAME_SIZE = 1024
const SAMPLE_RATE = 48000

const ANCHORS: Readonly<Record<string, readonly [number, number]>> = {
  A: [1.0, 0.6],
  I: [0.1, 0.7],
  U: [0.25, 0.1],
  E: [0.5, 0.7],
  O: [0.75, 0.1],
}

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
  return pcm
}

function featuresOf(f1: number, f2: number): Float32Array {
  const ex = createFeatureExtractor(SAMPLE_RATE, FRAME_SIZE)
  const mfcc = new Float32Array(MFCC_COEFFS)
  ex.extract(synthVowel(220, f1, f2), mfcc)
  const feat = new Float32Array(2)
  articulationFeatures(mfcc, feat)
  return feat
}

describe('mouthShape', () => {
  it('shapeWeights at each anchor: that viseme wins, weights sum to 1', () => {
    for (const v of VISEMES) {
      const out = new Float32Array(VISEMES.length)
      const [o, b] = ANCHORS[v]
      shapeWeights(o, b, out)
      let sum = 0
      for (let k = 0; k < out.length; k++) {
        sum += out[k]
      }
      expect(sum).toBeCloseTo(1, 6)
      const self = out[VISEMES.indexOf(v)]
      for (let k = 0; k < out.length; k++) {
        if (VISEMES[k] !== v) {
          expect(self).toBeGreaterThan(out[k])
        }
      }
    }
  })

  it('midpoint of I and E: I and E tie above the rest', () => {
    const out = new Float32Array(VISEMES.length)
    shapeWeights((0.1 + 0.5) / 2, 0.7, out)
    const i = out[VISEMES.indexOf('I')]
    const e = out[VISEMES.indexOf('E')]
    expect(i).toBeCloseTo(e, 12)
    for (const v of ['A', 'U', 'O'] as const) {
      expect(i).toBeGreaterThan(out[VISEMES.indexOf(v)])
    }
  })

  it('articulationFeatures: A more open than I, I brighter than U', () => {
    const a = featuresOf(850, 1220)
    const i = featuresOf(310, 2790)
    const u = featuresOf(370, 950)
    expect(a[0]).toBeGreaterThan(i[0])
    expect(i[1]).toBeGreaterThan(u[1])
  })

  it('fresh VoiceRange: default lo normalizes to 0, default hi to 1', () => {
    const range = new VoiceRange()
    const lo = new Float32Array(2)
    const hi = new Float32Array(2)
    range.normalize([5.95, -6.2], lo)
    range.normalize([6.77, 2.1], hi)
    expect(lo[0]).toBe(0)
    expect(lo[1]).toBe(0)
    expect(hi[0]).toBe(1)
    expect(hi[1]).toBe(1)
  })

  it('VoiceRange after alternating P/Q updates: P near 0, Q near 1', () => {
    const range = new VoiceRange()
    const P = [5.6, -5]
    const Q = [7.0, 5]
    for (let n = 0; n < 2000; n++) {
      range.update(n % 2 === 0 ? P : Q)
    }
    const np = new Float32Array(2)
    const nq = new Float32Array(2)
    range.normalize(P, np)
    range.normalize(Q, nq)
    expect(Math.abs(np[0])).toBeLessThan(0.15)
    expect(Math.abs(np[1])).toBeLessThan(0.15)
    expect(Math.abs(nq[0] - 1)).toBeLessThan(0.15)
    expect(Math.abs(nq[1] - 1)).toBeLessThan(0.15)
  })
})
