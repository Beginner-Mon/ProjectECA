import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CanonicalEmotion } from './AvatarProfile'
import type { ExpressionController } from './ExpressionController'
import type { EyeController } from './EyeController'
import { IdleBehaviorController } from './IdleBehaviorController'

interface Call {
  emotion: string
  intensity: number
  durationMs: number
}

const TICKS = 4000

function seededRandom(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0
    return state / 0x100000000
  }
}

function drive(binary?: readonly CanonicalEmotion[]): Call[] {
  const calls: Call[] = []
  const expression = {
    setEmotion: (emotion: string, intensity: number, durationMs: number) => {
      calls.push({ emotion, intensity, durationMs })
    },
  } as unknown as ExpressionController
  const eye = { setWander: () => {} } as unknown as EyeController
  const idle = new IdleBehaviorController(expression, eye, binary)
  for (let i = 0; i < TICKS; i++) idle.tick(10)
  return calls
}

describe('IdleBehaviorController emotion wanderer', () => {
  beforeEach(() => {
    vi.spyOn(Math, 'random').mockImplementation(seededRandom(12345))
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('plays every idle expression at full intensity', () => {
    const shown = drive().filter((c) => c.emotion !== 'neutral')
    expect(shown.length).toBeGreaterThan(1000)
    for (const c of shown) expect(c.intensity).toBe(1)
  })

  it('leans relaxed (about 62%) over happy, and uses both', () => {
    const shown = drive().filter((c) => c.emotion !== 'neutral')
    const relaxed = shown.filter((c) => c.emotion === 'relaxed').length
    const happy = shown.filter((c) => c.emotion === 'happy').length
    expect(relaxed).toBeGreaterThan(0)
    expect(happy).toBeGreaterThan(0)
    const share = relaxed / shown.length
    expect(share).toBeGreaterThanOrEqual(0.55)
    expect(share).toBeLessThanOrEqual(0.69)
  })

  it('always returns to neutral between two expressions', () => {
    const calls = drive()
    expect(calls.length).toBeGreaterThan(2000)
    calls.forEach((c, i) => {
      const isNeutral = c.emotion === 'neutral'
      expect(isNeutral).toBe(i % 2 === 1)
    })
  })

  it('never plays an emotion listed as binary', () => {
    const calls = drive(['relaxed'])
    expect(calls.some((c) => c.emotion === 'relaxed')).toBe(false)
    expect(calls.some((c) => c.emotion === 'happy')).toBe(true)
  })
})
