import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionMessage } from './api'
import { createTurnRecovery, pickSavedReply } from './turnRecovery'

const user = (content: string, id?: string): SessionMessage => ({
  id, role: 'user', content, timestamp: '2026-10-13T00:00:00Z',
})
const bot = (content: string, id?: string): SessionMessage => ({
  id, role: 'assistant', content, timestamp: '2026-10-13T00:00:01Z',
})
const none = new Set<string>()

describe('pickSavedReply', () => {
  it('finds the reply after the matching user message', () => {
    const msgs = [user('hi', 'u1'), bot('hello', 'a1')]
    expect(pickSavedReply(msgs, 'hi', none)?.id).toBe('a1')
  })

  it('ignores a pair whose reply is already on screen', () => {
    const msgs = [user('hi', 'u1'), bot('hello', 'a1')]
    expect(pickSavedReply(msgs, 'hi', new Set(['a1']))).toBeUndefined()
  })

  it('returns nothing when only the user message was saved', () => {
    expect(pickSavedReply([user('hi', 'u1')], 'hi', none)).toBeUndefined()
    // An older pair for other text does not count either.
    expect(pickSavedReply([user('x'), bot('y', 'a0'), user('hi')], 'hi', none)).toBeUndefined()
  })

  it('takes the latest pair when the same text was sent twice', () => {
    const msgs = [user('hi'), bot('first', 'a1'), user('hi'), bot('second', 'a2')]
    expect(pickSavedReply(msgs, 'hi', none)?.id).toBe('a2')
  })

  it('does not fall back to an earlier pair when the latest one is on screen', () => {
    const msgs = [user('hi'), bot('first', 'a1'), user('hi'), bot('second', 'a2')]
    expect(pickSavedReply(msgs, 'hi', new Set(['a2']))).toBeUndefined()
  })

  it('ignores a reply without a server id (nothing to attach or compare)', () => {
    expect(pickSavedReply([user('hi'), bot('hello')], 'hi', none)).toBeUndefined()
  })
})

describe('createTurnRecovery', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const setup = (results: Array<string | undefined | Error>, relevant = () => true) => {
    let n = 0
    const fetchFn = vi.fn(async () => {
      const r = results[Math.min(n++, results.length - 1)]
      if (r instanceof Error) throw r
      return r
    })
    const onRecovered = vi.fn()
    const rec = createTurnRecovery<string>({
      delays: [3000, 10000, 30000],
      fetch: fetchFn,
      isRelevant: relevant,
      onRecovered,
    })
    return { fetchFn, onRecovered, rec }
  }

  it('stops at the first success', async () => {
    const { fetchFn, onRecovered } = setup([undefined, 'reply'])
    await vi.advanceTimersByTimeAsync(3000)
    expect(fetchFn).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(7000) // t = 10 s
    expect(fetchFn).toHaveBeenCalledTimes(2)
    expect(onRecovered).toHaveBeenCalledWith('reply')
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchFn).toHaveBeenCalledTimes(2)
  })

  it('gives up after the last delay, treating a failed fetch as a miss', async () => {
    const { fetchFn, onRecovered } = setup([new Error('offline')])
    await vi.advanceTimersByTimeAsync(120000)
    expect(fetchFn).toHaveBeenCalledTimes(3)
    expect(onRecovered).not.toHaveBeenCalled()
  })

  it('stops when the relevance check turns false', async () => {
    let relevant = true
    const { fetchFn, onRecovered } = setup(['reply'], () => relevant)
    await vi.advanceTimersByTimeAsync(2000)
    relevant = false
    await vi.advanceTimersByTimeAsync(60000)
    expect(fetchFn).not.toHaveBeenCalled()
    expect(onRecovered).not.toHaveBeenCalled()
  })

  it('does not deliver a reply fetched after the conversation changed', async () => {
    let relevant = true
    let release: (v: string) => void = () => {}
    const onRecovered = vi.fn()
    createTurnRecovery<string>({
      delays: [3000],
      fetch: () => new Promise<string>((r) => { release = r }),
      isRelevant: () => relevant,
      onRecovered,
    })
    await vi.advanceTimersByTimeAsync(3000)
    relevant = false
    release('reply')
    await vi.advanceTimersByTimeAsync(0)
    expect(onRecovered).not.toHaveBeenCalled()
  })

  it('tryNow makes an immediate attempt', async () => {
    const { fetchFn, onRecovered, rec } = setup(['reply'])
    rec.tryNow()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchFn).toHaveBeenCalledTimes(1)
    expect(onRecovered).toHaveBeenCalledWith('reply')
  })

  it('cancel stops everything', async () => {
    const { fetchFn, rec } = setup(['reply'])
    rec.cancel()
    rec.tryNow()
    await vi.advanceTimersByTimeAsync(120000)
    expect(fetchFn).not.toHaveBeenCalled()
  })
})
