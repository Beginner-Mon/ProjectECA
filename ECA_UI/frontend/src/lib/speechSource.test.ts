import { afterEach, describe, expect, it, vi } from 'vitest'

/*
 * Synthesis in flight outlives the component that asked for it.
 *
 * Regression 24-09-2026: closing the conversation while the speaker spun
 * aborted the request — ~25s of synthesis thrown away, the button back to a
 * plain speaker, and the next click paying for all of it again. The audio
 * half never had this problem (speechPlayer is a singleton and kept playing
 * through the unmount); only the loading half did.
 *
 * api.ts pulls in aws-amplify, which does not load under vitest's `node`
 * environment — hence the mock. `speakText` is held open deliberately: these
 * tests are about what happens WHILE a synthesis is in flight.
 */

const speakText = vi.fn()

vi.mock('./api', () => ({
  cognitoSub: async () => null,
  speakText: (...args: unknown[]) => speakText(...args),
  DEFAULT_PERSONA_ID: 'anne',
}))

vi.mock('./ttsCache', () => ({
  cacheKeyFor: async () => null,
  readCachedClip: async () => null,
  writeCachedClip: async () => {},
  dropStaleVoice: async () => {},
}))

const { cancelSpeech, liveSpeech, openSpeech } = await import('./speechSource')

afterEach(() => {
  speakText.mockReset()
})

describe('synthesis in flight', () => {
  it('hands back the same clip instead of starting a second synthesis', async () => {
    speakText.mockImplementation(() => new Promise(() => {})) // never settles
    const first = await openSpeech('xin chào', 'anne')
    const second = await openSpeech('xin chào', 'anne')

    expect(second).toBe(first)
    expect(speakText).toHaveBeenCalledTimes(1)
    cancelSpeech('xin chào', 'anne')
  })

  it('is findable by message after the component that started it is gone', async () => {
    speakText.mockImplementation(() => new Promise(() => {}))
    const clip = await openSpeech('bài tập vai', 'anne')

    // No unmount hook, no abort: the request simply keeps going, and a
    // remounted button finds it.
    expect(liveSpeech('bài tập vai', 'anne')).toBe(clip)
    // A different message, or the same words in another voice, is not it.
    expect(liveSpeech('bài tập vai', 'reporter')).toBeNull()
    expect(liveSpeech('câu khác', 'anne')).toBeNull()
    cancelSpeech('bài tập vai', 'anne')
  })

  it('cancelling aborts the request and forgets the clip', async () => {
    let signal: AbortSignal | undefined
    speakText.mockImplementation((...args: unknown[]) => {
      signal = args[3] as AbortSignal
      return new Promise(() => {})
    })
    await openSpeech('dừng đi', 'anne')

    cancelSpeech('dừng đi', 'anne')

    expect(signal?.aborted).toBe(true)
    expect(liveSpeech('dừng đi', 'anne')).toBeNull()
  })

  it('a failed synthesis is not handed to the next click', async () => {
    speakText.mockImplementation(() => Promise.reject(new Error('503')))
    const clip = await openSpeech('hỏng', 'anne')
    await new Promise((r) => setTimeout(r, 0))

    expect(clip.status).toBe('failed')
    expect(liveSpeech('hỏng', 'anne')).toBeNull()
  })
})
