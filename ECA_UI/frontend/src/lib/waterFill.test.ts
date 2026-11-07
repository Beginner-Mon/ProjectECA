import { beforeEach, describe, expect, it } from 'vitest'
import { WAIT_LEVEL, _resetFillClock, beginFill, endFill, fillLevel, levelToY } from './waterFill'

describe('fillLevel', () => {
  it('starts empty and rises slowly', () => {
    expect(fillLevel(0)).toBe(0)
    expect(fillLevel(1000)).toBeGreaterThan(0)
    expect(fillLevel(1000)).toBeLessThan(0.2)
  })

  it('keeps rising but never reaches the waiting level', () => {
    expect(fillLevel(12000)).toBeGreaterThan(fillLevel(6000))
    expect(fillLevel(600000)).toBeLessThanOrEqual(WAIT_LEVEL)
    expect(fillLevel(600000)).toBeGreaterThan(WAIT_LEVEL - 0.01)
  })
})

describe('levelToY', () => {
  it('maps empty below the logo and full above it', () => {
    expect(levelToY(0)).toBeGreaterThan(543)
    expect(levelToY(1)).toBeLessThan(0)
    expect(levelToY(2)).toBe(levelToY(1))
  })
})

describe('shared fill clock', () => {
  beforeEach(() => _resetFillClock())

  it('carries the level over when one loader replaces another', () => {
    const first = beginFill(1000)
    endFill(5000)
    expect(beginFill(5000)).toBe(first)
  })

  it('starts again for a later, separate wait', () => {
    beginFill(1000)
    endFill(5000)
    expect(beginFill(20000)).toBe(20000)
  })

  it('two loaders at once share one start', () => {
    const a = beginFill(1000)
    expect(beginFill(3000)).toBe(a)
  })
})
