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

const TICKS = 100000

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
  for (let i = 0; i < TICKS; i++) idle.tick(1)
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

  it('shows relaxed only, never happy', () => {
    const shown = drive().filter((c) => c.emotion !== 'neutral')
    expect(shown.length).toBeGreaterThan(0)
    for (const c of shown) expect(c.emotion).toBe('relaxed')
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
  })

  describe('timing', () => {
    function rig() {
      const log: Array<{ at: number; emotion: string }> = []
      let now = 0
      const expression = {
        setEmotion: (emotion: string) => {
          log.push({ at: now, emotion })
        },
      } as unknown as ExpressionController
      const eye = { setWander: () => {} } as unknown as EyeController
      const idle = new IdleBehaviorController(expression, eye)
      const run = (seconds: number) => {
        for (let i = 0; i < seconds; i++) {
          now += 1
          idle.tick(1)
        }
      }
      return { idle, log, run, clock: () => now }
    }

    function expectCycles(log: Array<{ at: number; emotion: string }>, start: number) {
      expect(log.length).toBeGreaterThanOrEqual(8)
      let prev = start
      log.forEach((c, i) => {
        const gap = c.at - prev
        if (i % 2 === 0) {
          expect(c.emotion).toBe('relaxed')
          expect(gap).toBeGreaterThanOrEqual(50)
          expect(gap).toBeLessThanOrEqual(60)
        } else {
          expect(c.emotion).toBe('neutral')
          expect(gap).toBeGreaterThanOrEqual(4)
          expect(gap).toBeLessThanOrEqual(9)
        }
        prev = c.at
      })
    }

    it('waits 50-60 s before an expression, then holds it 4-9 s, over several cycles', () => {
      const { log, run } = rig()
      run(600)
      expectCycles(log, 0)
    })

    it('waits 50-60 s again after reset()', () => {
      const { idle, log, run, clock } = rig()
      run(200)
      idle.reset()
      log.length = 0
      const start = clock()
      run(600)
      expectCycles(log, start)
    })
  })
})
