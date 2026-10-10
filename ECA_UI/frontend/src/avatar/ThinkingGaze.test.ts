import { describe, expect, it } from 'vitest'
import { ThinkingGaze } from './ThinkingGaze'

function recorder() {
  const calls: { x: number; y: number }[] = []
  return { calls, eye: { setOverride: (x: number, y: number) => calls.push({ x, y }) } }
}

/** Deterministic pseudo-random sequence. */
function seeded(seed = 7) {
  let s = seed
  return () => {
    s = (s * 16807) % 2147483647
    return (s - 1) / 2147483646
  }
}

describe('ThinkingGaze', () => {
  it('starts by looking up and to one side', () => {
    const { calls, eye } = recorder()
    new ThinkingGaze(eye, seeded()).start()
    expect(calls).toHaveLength(1)
    expect(calls[0].y).toBeGreaterThan(0.4) // up
    expect(Math.abs(calls[0].x)).toBeGreaterThan(0.3) // aside
  })

  it('keeps the eyes moving (micro-saccades), never frozen for long', () => {
    const { calls, eye } = recorder()
    const gaze = new ThinkingGaze(eye, seeded())
    gaze.start()
    let longestStill = 0
    let since = 0
    let last = calls.length
    for (let t = 0; t < 20; t += 1 / 60) {
      gaze.tick(1 / 60)
      since += 1 / 60
      if (calls.length !== last) {
        longestStill = Math.max(longestStill, since)
        since = 0
        last = calls.length
      }
    }
    expect(longestStill).toBeLessThan(1.2)
  })

  it('mostly looks away, sometimes glances back, and uses both sides', () => {
    const { calls, eye } = recorder()
    const gaze = new ThinkingGaze(eye, seeded(11))
    gaze.start()
    for (let t = 0; t < 120; t += 1 / 60) gaze.tick(1 / 60)
    const up = calls.filter((c) => c.y > 0.3).length
    const back = calls.filter((c) => Math.abs(c.x) < 0.15 && c.y < 0.2).length
    expect(up / calls.length).toBeGreaterThan(0.7)
    expect(back).toBeGreaterThan(0)
    expect(calls.some((c) => c.x > 0.3)).toBe(true)
    expect(calls.some((c) => c.x < -0.3)).toBe(true)
  })
})
