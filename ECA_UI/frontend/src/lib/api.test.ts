import { describe, expect, it, vi } from 'vitest'

vi.mock('aws-amplify/auth', () => ({ fetchAuthSession: vi.fn(async () => ({})) }))

import { StreamInterruptedError, streamChat } from './api'

/*
 * A fake /chat response: the body is a list of chunks, optionally followed by a
 * read failure. clone().text() is what the buffered-proxy fallback reads.
 */
const enc = new TextEncoder()
const block = (type: string, data: unknown) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`

function fakeResponse(chunks: string[], failWith?: Error, fullText = chunks.join('')) {
  const make = () => {
    let i = 0
    return {
      read: async () => {
        if (i < chunks.length) return { done: false, value: enc.encode(chunks[i++]) }
        if (failWith) throw failWith
        return { done: true, value: undefined }
      },
    }
  }
  const resp = {
    ok: true,
    body: { getReader: make },
    clone: () => ({ text: async () => fullText }),
  }
  vi.stubGlobal('fetch', vi.fn(async () => resp))
}

const run = (signal?: AbortSignal) => {
  const events: string[] = []
  const p = streamChat({ query: 'hi', sessionId: 's1' }, (t) => events.push(t), signal)
  return { events, p }
}

describe('streamChat interruptions', () => {
  it('reader fails before any event: falls back to the clone, as before', async () => {
    const full = block('token', { content: 'a' }) + block('done', {})
    fakeResponse([], new Error('boom'), full)
    const { events, p } = run()
    await p
    expect(events).toEqual(['token', 'done'])
  })

  it('reader fails after an event: throws interrupted, nothing is replayed', async () => {
    const full = block('token', { content: 'a' }) + block('done', {})
    fakeResponse([block('token', { content: 'a' })], new Error('boom'), full)
    const { events, p } = run()
    await expect(p).rejects.toBeInstanceOf(StreamInterruptedError)
    expect(events).toEqual(['token'])
  })

  it('stream ends without done: interrupted', async () => {
    fakeResponse([block('token', { content: 'a' })])
    const { events, p } = run()
    await expect(p).rejects.toBeInstanceOf(StreamInterruptedError)
    expect(events).toEqual(['token'])
  })

  it('stream ends after done: resolves', async () => {
    fakeResponse([block('token', { content: 'a' }), block('done', {})])
    const { events, p } = run()
    await expect(p).resolves.toBeUndefined()
    expect(events).toEqual(['token', 'done'])
  })

  it('abort: AbortError passes through unchanged', async () => {
    const abort = Object.assign(new Error('aborted'), { name: 'AbortError' })
    fakeResponse([block('token', { content: 'a' })], abort)
    const { p } = run()
    await expect(p).rejects.toMatchObject({ name: 'AbortError' })
  })

  it('abort that ends the stream cleanly is not an interruption', async () => {
    fakeResponse([block('token', { content: 'a' })])
    const ctl = new AbortController()
    ctl.abort()
    const { p } = run(ctl.signal)
    await expect(p).resolves.toBeUndefined()
  })
})
