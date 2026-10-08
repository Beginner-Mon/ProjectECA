/**
 * MFCC feature extraction for mouth-shape lip sync.
 *
 * Each audio frame is reduced to 12 MFCCs (cepstral indices 1..12, C0
 * dropped so the features are amplitude-invariant), feeding
 * articulationFeatures() in mouthShape.ts. Nothing here touches the DOM,
 * audio hardware, or any runtime controller.
 *
 * Constraints: erasable syntax only (no enum/namespace/parameter
 * properties). `extract` allocates nothing; all buffers live in the closure
 * created by `createFeatureExtractor`.
 */

/** MFCC coefficients per frame. */
export const MFCC_COEFFS = 12

export interface FeatureExtractor {
  /**
   * Write the frame MFCC into `out` (length MFCC_COEFFS).
   * Returns the low-band (100–1000 Hz) power, used to pick vowel frames.
   */
  extract(pcm: Float32Array, out: Float32Array): number
}

const MEL_FILTER_COUNT = 24
const MEL_LOW_HZ = 100
const MEL_HIGH_HZ = 5000
const LOW_BAND_LOW_HZ = 100
const LOW_BAND_HIGH_HZ = 1000
const LOG_FLOOR = 1e-10

function mel(f: number): number {
  return 2595 * Math.log10(1 + f / 700)
}

function invMel(m: number): number {
  return 700 * (10 ** (m / 2595) - 1)
}

export function createFeatureExtractor(sampleRate: number, frameSize: number): FeatureExtractor {
  if (!Number.isInteger(frameSize) || frameSize <= 0 || (frameSize & (frameSize - 1)) !== 0) {
    throw new Error(`mfcc: frameSize must be a power of 2, got ${frameSize}`)
  }
  const half = frameSize / 2
  const binCount = half + 1

  // Hann window.
  const hann = new Float64Array(frameSize)
  for (let n = 0; n < frameSize; n++) {
    hann[n] = 0.5 - 0.5 * Math.cos((2 * Math.PI * n) / (frameSize - 1))
  }

  // Bit-reversal table for the radix-2 FFT.
  const rev = new Uint32Array(frameSize)
  const bits = Math.log2(frameSize)
  for (let i = 0; i < frameSize; i++) {
    let r = 0
    for (let b = 0; b < bits; b++) {
      r = (r << 1) | ((i >>> b) & 1)
    }
    rev[i] = r
  }

  // Mel filterbank: 24 triangular filters whose edges are equally spaced on
  // the mel scale from 100 Hz to 5000 Hz. Edges are in Hz, so the bank does
  // not depend on the sample rate — only the bin sampling does.
  const edgeCount = MEL_FILTER_COUNT + 2
  const melLow = mel(MEL_LOW_HZ)
  const melHigh = mel(MEL_HIGH_HZ)
  const edges = new Float64Array(edgeCount)
  for (let i = 0; i < edgeCount; i++) {
    edges[i] = invMel(melLow + ((melHigh - melLow) * i) / (edgeCount - 1))
  }
  const filterbank = new Float64Array(MEL_FILTER_COUNT * binCount)
  for (let m = 0; m < MEL_FILTER_COUNT; m++) {
    const left = edges[m]
    const center = edges[m + 1]
    const right = edges[m + 2]
    for (let k = 0; k < binCount; k++) {
      const f = (k * sampleRate) / frameSize
      let w = 0
      if (f > left && f < right) {
        w = f <= center ? (f - left) / (center - left) : (right - f) / (right - center)
      }
      filterbank[m * binCount + k] = w
    }
  }

  // DCT-II rows for cepstral indices 1..12.
  const dct = new Float64Array(MFCC_COEFFS * MEL_FILTER_COUNT)
  for (let i = 1; i <= MFCC_COEFFS; i++) {
    for (let m = 0; m < MEL_FILTER_COUNT; m++) {
      dct[(i - 1) * MEL_FILTER_COUNT + m] = Math.cos((Math.PI * i * (m + 0.5)) / MEL_FILTER_COUNT)
    }
  }

  // FFT bin range covering 100–1000 Hz.
  const kLow = Math.max(0, Math.ceil((LOW_BAND_LOW_HZ * frameSize) / sampleRate))
  const kHigh = Math.min(half, Math.floor((LOW_BAND_HIGH_HZ * frameSize) / sampleRate))

  // Scratch buffers — allocated once; extract() allocates nothing.
  const real = new Float64Array(frameSize)
  const imag = new Float64Array(frameSize)
  const power = new Float64Array(binCount)
  const logE = new Float64Array(MEL_FILTER_COUNT)

  function fft(): void {
    for (let i = 0; i < frameSize; i++) {
      const j = rev[i]
      if (j > i) {
        const tr = real[i]
        real[i] = real[j]
        real[j] = tr
        const ti = imag[i]
        imag[i] = imag[j]
        imag[j] = ti
      }
    }
    for (let len = 2; len <= frameSize; len *= 2) {
      const angle = (-2 * Math.PI) / len
      const wlenR = Math.cos(angle)
      const wlenI = Math.sin(angle)
      const halfLen = len / 2
      for (let i = 0; i < frameSize; i += len) {
        let wR = 1
        let wI = 0
        for (let j = 0; j < halfLen; j++) {
          const uR = real[i + j]
          const uI = imag[i + j]
          const eR = real[i + j + halfLen]
          const eI = imag[i + j + halfLen]
          const vR = eR * wR - eI * wI
          const vI = eR * wI + eI * wR
          real[i + j] = uR + vR
          imag[i + j] = uI + vI
          real[i + j + halfLen] = uR - vR
          imag[i + j + halfLen] = uI - vI
          const nextWR = wR * wlenR - wI * wlenI
          wI = wR * wlenI + wI * wlenR
          wR = nextWR
        }
      }
    }
  }

  function extract(pcm: Float32Array, out: Float32Array): number {
    const n = Math.min(pcm.length, frameSize)
    for (let i = 0; i < frameSize; i++) {
      real[i] = (i < n ? pcm[i] : 0) * hann[i]
      imag[i] = 0
    }
    fft()
    for (let k = 0; k < binCount; k++) {
      power[k] = real[k] * real[k] + imag[k] * imag[k]
    }
    let lowBand = 0
    for (let k = kLow; k <= kHigh; k++) {
      lowBand += power[k]
    }
    for (let m = 0; m < MEL_FILTER_COUNT; m++) {
      let e = 0
      const row = m * binCount
      for (let k = 0; k < binCount; k++) {
        e += power[k] * filterbank[row + k]
      }
      logE[m] = Math.log(e + LOG_FLOOR)
    }
    for (let i = 0; i < MFCC_COEFFS; i++) {
      let c = 0
      const row = i * MEL_FILTER_COUNT
      for (let m = 0; m < MEL_FILTER_COUNT; m++) {
        c += logE[m] * dct[row + m]
      }
      out[i] = c
    }
    return lowBand
  }

  return { extract }
}
