import { describe, expect, it } from 'vitest'
import { parseReplyEmotion } from './replyEmotion'

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
