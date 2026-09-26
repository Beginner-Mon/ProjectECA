import { describe, expect, it } from 'vitest'
import { GestureCameraTrack } from './GestureCameraTrack'

const ZOOM = [
  { t: 0, value: 1 },
  { t: 1.4, value: 1 },
  { t: 1.9, value: 0.7 },
  { t: 2.8, value: 0.7 },
  { t: 3.4, value: 1 },
]

function run(track: GestureCameraTrack, seconds: number, step = 1 / 60) {
  for (let t = 0; t < seconds - 1e-9; t += step) track.tick(Math.min(step, seconds - t))
}

describe('GestureCameraTrack — zoom timed to a gesture (the kiss close-up)', () => {
  it('is 1 (no zoom) when nothing is playing', () => {
    expect(new GestureCameraTrack().value).toBe(1)
  })

  it('holds the wide framing through the first half', () => {
    const z = new GestureCameraTrack()
    z.play(ZOOM)
    run(z, 1.2)
    expect(z.value).toBeCloseTo(1, 5)
  })

  it('is fully zoomed in for the second half', () => {
    const z = new GestureCameraTrack()
    z.play(ZOOM)
    run(z, 2.3)
    expect(z.value).toBeCloseTo(0.7, 5)
  })

  it('eases between keys, not jumping', () => {
    const z = new GestureCameraTrack()
    z.play(ZOOM)
    run(z, 1.65)
    expect(z.value).toBeGreaterThan(0.7)
    expect(z.value).toBeLessThan(1)
  })

  it('returns to 1 after the last key', () => {
    const z = new GestureCameraTrack()
    z.play(ZOOM)
    run(z, 3.6)
    expect(z.value).toBe(1)
  })

  it('stop() eases back to 1 from wherever it is (gesture interrupted)', () => {
    const z = new GestureCameraTrack()
    z.play(ZOOM)
    run(z, 2.3)
    z.stop()
    run(z, 0.1)
    expect(z.value).toBeGreaterThan(0.7)
    expect(z.value).toBeLessThan(1)
    run(z, 0.5)
    expect(z.value).toBe(1)
  })

  it('ignores an empty or single-key track', () => {
    const z = new GestureCameraTrack()
    z.play([])
    expect(z.value).toBe(1)
    z.play([{ t: 0, value: 0.5 }])
    expect(z.value).toBe(1)
  })
})

describe('GestureCameraTrack with a rest value of 0 (the partner-view weight)', () => {
  it('rests at 0, rises to 1 during the hold, and falls back to 0', () => {
    const w = new GestureCameraTrack(0)
    expect(w.value).toBe(0)
    w.play([{ t: 0, value: 0 }, { t: 0.8, value: 0 }, { t: 1.45, value: 1 }, { t: 2.45, value: 1 }, { t: 3.2, value: 0 }])
    run(w, 2.0)
    expect(w.value).toBeCloseTo(1, 5)
    run(w, 1.4)
    expect(w.value).toBe(0)
  })

  it('stop() eases back to the rest value, not to 1', () => {
    const w = new GestureCameraTrack(0)
    w.play([{ t: 0, value: 1 }, { t: 3, value: 1 }])
    run(w, 1)
    w.stop()
    run(w, 1)
    expect(w.value).toBe(0)
  })
})
