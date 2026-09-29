import { describe, expect, it } from 'vitest'

import { canSubmit, visibleReasons } from './dislikeFeedbackLogic'

describe('visibleReasons', () => {
  it('always includes the reasons that apply to every reply', () => {
    const always = ['incorrect', 'unsafe', 'not_relevant', 'incomplete', 'hard_to_follow', 'wrong_language', 'other']
    const reasons = visibleReasons(false, false)
    for (const code of always) expect(reasons).toContain(code)
  })

  it('hides motion_issue when the reply has no motion', () => {
    expect(visibleReasons(false, true)).not.toContain('motion_issue')
  })

  it('shows motion_issue when the reply has a motion', () => {
    expect(visibleReasons(true, false)).toContain('motion_issue')
  })

  it('hides voice_issue when the reply has no speech clip', () => {
    expect(visibleReasons(true, false)).not.toContain('voice_issue')
  })

  it('shows voice_issue when the reply has a speech clip', () => {
    expect(visibleReasons(false, true)).toContain('voice_issue')
  })

  it('shows both when the reply has motion and speech', () => {
    const reasons = visibleReasons(true, true)
    expect(reasons).toContain('motion_issue')
    expect(reasons).toContain('voice_issue')
  })
})

describe('canSubmit', () => {
  it('is false with no reasons and no comment', () => {
    expect(canSubmit([], '')).toBe(false)
  })

  it('is false for whitespace-only comment', () => {
    expect(canSubmit([], '   \n\t ')).toBe(false)
  })

  it('is true with at least one reason chip selected', () => {
    expect(canSubmit(['incorrect'], '')).toBe(true)
  })

  it('is true with non-blank comment text alone', () => {
    expect(canSubmit([], 'the exercise looked wrong')).toBe(true)
  })

  it('is true with both a reason and a comment', () => {
    expect(canSubmit(['unsafe'], 'could hurt my back')).toBe(true)
  })
})
