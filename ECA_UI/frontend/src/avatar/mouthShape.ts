import type { Viseme } from './AvatarProfile'

/**
 * Language-independent mouth shape (Phase B3, DEV-only).
 *
 * Instead of matching frames against per-voice templates, this measures two
 * physical quantities present in every language — openness (how high the
 * low-frequency energy sits) and brightness (high vs low band energy,
 * flat vs rounded lips) — normalises them against the voice currently
 * speaking (learned at runtime), and blends five fixed anchors.
 *
 * Pure math. Module-level lookup tables; no function allocates when called.
 * Viseme order is uniform everywhere in this file: out[k] is VISEMES[k].
 */

/** Viseme order for every 5-element vector in this file. */
export const VISEMES: readonly Viseme[] = ['A', 'I', 'U', 'E', 'O']

const MEL_BANDS = 24
const MEL_LOW_HZ = 100
const MEL_HIGH_HZ = 5000
const MFCC_KEEP = 12

function mel(f: number): number {
  return 2595 * Math.log10(1 + f / 700)
}

function invMel(m: number): number {
  return 700 * (10 ** (m / 2595) - 1)
}

// Band centres: the 24 interior mel edges, same scale as vowelClassifier.ts.
const MEL_LOW = mel(MEL_LOW_HZ)
const MEL_HIGH = mel(MEL_HIGH_HZ)
const CENTERS: readonly number[] = Array.from(
  { length: MEL_BANDS },
  (_, m) => invMel(MEL_LOW + ((MEL_HIGH - MEL_LOW) * (m + 1)) / (MEL_BANDS + 1)),
)
const LOG_CENTERS: readonly number[] = CENTERS.map((c) => Math.log(c))

// Inverse-DCT rows (cepstral indices 1..12) for rebuilding the smoothed
// log spectrum. IDCT[m * 12 + (i - 1)] = cos(π·i·(m + 0.5)/24).
const IDCT: readonly number[] = Array.from({ length: MEL_BANDS * MFCC_KEEP }, (_, k) => {
  const m = Math.floor(k / MFCC_KEEP)
  const i = (k % MFCC_KEEP) + 1
  return Math.cos((Math.PI * i * (m + 0.5)) / MEL_BANDS)
})

const LOW_LO_HZ = 250
const LOW_HI_HZ = 1200
const HIGH_LO_HZ = 1600
const HIGH_HI_HZ = 3500

// Band indices inside each range, fixed at module load.
const LOW_IDX: readonly number[] = CENTERS.map((c, m) =>
  c >= LOW_LO_HZ && c <= LOW_HI_HZ ? m : -1,
).filter((m) => m >= 0)
const HIGH_IDX: readonly number[] = CENTERS.map((c, m) =>
  c >= HIGH_LO_HZ && c <= HIGH_HI_HZ ? m : -1,
).filter((m) => m >= 0)

/**
 * Articulation features from 12 MFCCs (as written by
 * createFeatureExtractor(...).extract). out[0] = openness, out[1] = brightness.
 */
export function articulationFeatures(mfcc: ArrayLike<number>, out: Float32Array): void {
  let num = 0
  let den = 0
  for (let j = 0; j < LOW_IDX.length; j++) {
    const m = LOW_IDX[j]
    let l = 0
    for (let i = 1; i <= MFCC_KEEP; i++) {
      l += mfcc[i - 1] * IDCT[m * MFCC_KEEP + (i - 1)]
    }
    l /= MFCC_KEEP
    const w = Math.exp(l)
    num += w * LOG_CENTERS[m]
    den += w
  }
  out[0] = den > 0 ? num / den : 0

  let highSum = 0
  for (let j = 0; j < HIGH_IDX.length; j++) {
    const m = HIGH_IDX[j]
    let l = 0
    for (let i = 1; i <= MFCC_KEEP; i++) {
      l += mfcc[i - 1] * IDCT[m * MFCC_KEEP + (i - 1)]
    }
    highSum += l / MFCC_KEEP
  }
  let lowSum = 0
  for (let j = 0; j < LOW_IDX.length; j++) {
    const m = LOW_IDX[j]
    let l = 0
    for (let i = 1; i <= MFCC_KEEP; i++) {
      l += mfcc[i - 1] * IDCT[m * MFCC_KEEP + (i - 1)]
    }
    lowSum += l / MFCC_KEEP
  }
  const highMean = HIGH_IDX.length > 0 ? highSum / HIGH_IDX.length : 0
  const lowMean = LOW_IDX.length > 0 ? lowSum / LOW_IDX.length : 0
  out[1] = highMean - lowMean
}

// ── Voice range: learns the speaking voice's value span at runtime ───────

const HIST_BINS = 64
const OPEN_LO = 5.3
const OPEN_HI = 7.4
const BRIGHT_LO = -14
const BRIGHT_HI = 10
const DECAY = 0.9992
const TRUST_FRAMES = 480
const DEF_OPEN_LO = 5.95
const DEF_OPEN_HI = 6.77
const DEF_BRIGHT_LO = -6.2
const DEF_BRIGHT_HI = 2.1
const MIN_OPEN_WIDTH = 0.3
const MIN_BRIGHT_WIDTH = 2.0

function pushHist(hist: Float32Array, x: number, lo: number, hi: number): void {
  for (let i = 0; i < hist.length; i++) {
    hist[i] *= DECAY
  }
  let idx = Math.floor(((x - lo) / (hi - lo)) * hist.length)
  if (idx < 0) {
    idx = 0
  } else if (idx >= hist.length) {
    idx = hist.length - 1
  }
  hist[idx] += 1
}

function histQuantile(
  hist: Float32Array,
  total: number,
  q: number,
  lo: number,
  hi: number,
): number {
  const target = q * total
  let cum = 0
  for (let i = 0; i < hist.length; i++) {
    cum += hist[i]
    if (cum >= target) {
      const frac = hist[i] > 0 ? (target - (cum - hist[i])) / hist[i] : 0
      return lo + ((i + frac) / hist.length) * (hi - lo)
    }
  }
  return hi
}

export class VoiceRange {
  private readonly open = new Float32Array(HIST_BINS)
  private readonly bright = new Float32Array(HIST_BINS)
  private total = 0

  /** Feed one voiced frame's features. */
  update(features: ArrayLike<number>): void {
    this.total = this.total * DECAY + 1
    pushHist(this.open, features[0], OPEN_LO, OPEN_HI)
    pushHist(this.bright, features[1], BRIGHT_LO, BRIGHT_HI)
  }

  /** (x − lo)/(hi − lo) per quantity, NOT clamped to 0..1. */
  normalize(features: ArrayLike<number>, out: Float32Array): void {
    const trust = Math.min(1, this.total / TRUST_FRAMES)
    let oLo =
      DEF_OPEN_LO + (histQuantile(this.open, this.total, 0.1, OPEN_LO, OPEN_HI) - DEF_OPEN_LO) * trust
    let oHi =
      DEF_OPEN_HI + (histQuantile(this.open, this.total, 0.9, OPEN_LO, OPEN_HI) - DEF_OPEN_HI) * trust
    if (oHi - oLo < MIN_OPEN_WIDTH) {
      const mid = (oLo + oHi) / 2
      oLo = mid - MIN_OPEN_WIDTH / 2
      oHi = mid + MIN_OPEN_WIDTH / 2
    }
    let bLo =
      DEF_BRIGHT_LO +
      (histQuantile(this.bright, this.total, 0.1, BRIGHT_LO, BRIGHT_HI) - DEF_BRIGHT_LO) * trust
    let bHi =
      DEF_BRIGHT_HI +
      (histQuantile(this.bright, this.total, 0.9, BRIGHT_LO, BRIGHT_HI) - DEF_BRIGHT_HI) * trust
    if (bHi - bLo < MIN_BRIGHT_WIDTH) {
      const mid = (bLo + bHi) / 2
      bLo = mid - MIN_BRIGHT_WIDTH / 2
      bHi = mid + MIN_BRIGHT_WIDTH / 2
    }
    out[0] = (features[0] - oLo) / (oHi - oLo)
    out[1] = (features[1] - bLo) / (bHi - bLo)
  }

  reset(): void {
    this.open.fill(0)
    this.bright.fill(0)
    this.total = 0
  }
}

/** Shared instance: survives controller recreation on model switch. */
export const sharedVoiceRange: VoiceRange = new VoiceRange()

// ── Fixed anchors: normalised (openness, brightness) per viseme ─────────

const ANCHOR_A: readonly [number, number] = [1.0, 0.6]
const ANCHOR_I: readonly [number, number] = [0.1, 0.7]
const ANCHOR_U: readonly [number, number] = [0.25, 0.1]
const ANCHOR_E: readonly [number, number] = [0.5, 0.7]
const ANCHOR_O: readonly [number, number] = [0.75, 0.1]
const SHAPE_SIGMA = 0.22

const ANCHOR_OPEN: readonly number[] = [
  ANCHOR_A[0],
  ANCHOR_I[0],
  ANCHOR_U[0],
  ANCHOR_E[0],
  ANCHOR_O[0],
]
const ANCHOR_BRIGHT: readonly number[] = [
  ANCHOR_A[1],
  ANCHOR_I[1],
  ANCHOR_U[1],
  ANCHOR_E[1],
  ANCHOR_O[1],
]

/**
 * Blend weights from normalised (openness, brightness). Gaussian falloff
 * around each anchor, normalised; out[k] sums to 1 in VISEMES order.
 */
export function shapeWeights(openness: number, brightness: number, out: Float32Array): void {
  const denom = 2 * SHAPE_SIGMA * SHAPE_SIGMA
  let sum = 0
  for (let k = 0; k < VISEMES.length; k++) {
    const dx = openness - ANCHOR_OPEN[k]
    const db = brightness - ANCHOR_BRIGHT[k]
    const w = Math.exp(-(dx * dx + db * db) / denom)
    out[k] = w
    sum += w
  }
  if (sum > 0) {
    for (let k = 0; k < VISEMES.length; k++) {
      out[k] /= sum
    }
  }
}
