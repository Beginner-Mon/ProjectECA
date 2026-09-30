import { describe, expect, it } from 'vitest'

import { canSubmit, visibleReasons } from './dislikeFeedbackLogic'

describe('visibleReasons', () => {
  it('always includes the reasons that apply to every reply, including voice_issue', () => {
    const always = [
      'incorrect', 'unsafe', 'not_relevant', 'incomplete', 'hard_to_follow',
      'wrong_language', 'voice_issue', 'other',
    ]
    const reasons = visibleReasons(false)
    for (const code of always) expect(reasons).toContain(code)
  })

  it('hides motion_issue when the reply has no motion', () => {
    expect(visibleReasons(false)).not.toContain('motion_issue')
  })

  it('shows motion_issue when the reply has a motion', () => {
    expect(visibleReasons(true)).toContain('motion_issue')
  })

  it('shows voice_issue regardless of motion', () => {
    expect(visibleReasons(true)).toContain('voice_issue')
    expect(visibleReasons(false)).toContain('voice_issue')
  })
})

describe('canSubmit', () => {
  it('is false with no reason and no comment', () => {
    expect(canSubmit('', '')).toBe(false)
  })

  it('is false for whitespace-only comment', () => {
    expect(canSubmit('', '   \n\t ')).toBe(false)
  })

  it('is true with a reason chosen', () => {
    expect(canSubmit('incorrect', '')).toBe(true)
  })

  it('is true with non-blank comment text alone', () => {
    expect(canSubmit('', 'the exercise looked wrong')).toBe(true)
  })

  it('is true with both a reason and a comment', () => {
    expect(canSubmit('unsafe', 'could hurt my back')).toBe(true)
  })
})
