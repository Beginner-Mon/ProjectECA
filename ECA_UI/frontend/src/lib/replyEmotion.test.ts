import { describe, expect, it } from 'vitest'
import { isSafetyTurn, listeningFace, parseReplyEmotion } from './replyEmotion'

describe('parseReplyEmotion — the `emotion` SSE event from the backend', () => {
  it('accepts a canonical emotion with an intensity', () => {
    expect(parseReplyEmotion({ name: 'sad', intensity: 0.4 })).toEqual({ name: 'sad', intensity: 0.4 })
  })

  it('defaults a missing intensity to moderate', () => {
    expect(parseReplyEmotion({ name: 'happy' })).toEqual({ name: 'happy', intensity: 0.6 })
  })

  it('clamps intensity into 0..1', () => {
    expect(parseReplyEmotion({ name: 'happy', intensity: 3 })!.intensity).toBe(1)
    expect(parseReplyEmotion({ name: 'happy', intensity: -1 })!.intensity).toBe(0)
  })

  it('ignores anything that is not a known emotion — a bad event never breaks the face', () => {
    expect(parseReplyEmotion({ name: 'ecstatic', intensity: 1 })).toBeNull()
    expect(parseReplyEmotion({ intensity: 1 })).toBeNull()
    expect(parseReplyEmotion(null)).toBeNull()
    expect(parseReplyEmotion('happy')).toBeNull()
  })
})

describe('listeningFace — the planner `user_affect` while the avatar thinks', () => {
  it('shows gentle concern for pain, worry and sad news', () => {
    for (const a of ['pain', 'worry', 'sad_news']) expect(listeningFace(a)).toEqual({ name: 'sad', intensity: 0.3 })
  })

  it('smiles for thanks, greetings and good news', () => {
    for (const a of ['thanks', 'greeting', 'good_news']) expect(listeningFace(a)).toEqual({ name: 'happy', intensity: 0.4 })
  })

  it('leaves the face alone for neutral, unknown or missing affect', () => {
    expect(listeningFace('neutral')).toBeNull()
    expect(listeningFace('furious')).toBeNull()
    expect(listeningFace(undefined)).toBeNull()
  })
})

describe('isSafetyTurn', () => {
  it('is true for red-flag and referral turns only', () => {
    expect(isSafetyTurn(['red_flag_screen', 'referral_advice'])).toBe(true)
    expect(isSafetyTurn(['referral_advice'])).toBe(true)
    expect(isSafetyTurn(['scope_disclaimer'])).toBe(false)
    expect(isSafetyTurn(undefined)).toBe(false)
  })
})
