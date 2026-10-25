import { describe, expect, it, vi } from 'vitest'

vi.mock('aws-amplify/auth', () => ({ fetchAuthSession: vi.fn(async () => ({})) }))

import { HttpStatusError, SSE_IDLE_TIMEOUT_MS, StreamInterruptedError, StreamTimeoutError, streamChat } from './api'

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

describe('streamChat token_limit', () => {
  const LLM = {
    base_url: 'https://p.example/v1',
    model: 'm',
    api_key: 'k',
    capabilities: ['text' as const],
    omit: [],
  }
  const sentBody = async (opts: Partial<Parameters<typeof streamChat>[0]>) => {
    fakeResponse([block('done', {})])
    await streamChat({ query: 'hi', sessionId: 's1', ...opts }, () => {})
    const init = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1] as { body: string }
    return JSON.parse(init.body) as Record<string, unknown>
  }

  it('adds token_limit at the top level, never inside llm', async () => {
    const body = await sentBody({ llm: LLM, tokenLimit: 128000 })
    expect(body.token_limit).toBe(128000)
    expect(body.llm).toEqual(LLM)
    expect(body.llm).not.toHaveProperty('token_limit')
  })

  it('omits the key when not given or not a positive integer', async () => {
    for (const tokenLimit of [undefined, 0, -1, 1.5, NaN]) {
      const body = await sentBody({ llm: LLM, tokenLimit })
      expect(body).not.toHaveProperty('token_limit')
    }
  })
})

describe('streamChat errors (E2)', () => {
  it('non-2xx throws HttpStatusError with the status; the body is never read', async () => {
    const text = vi.fn(async () => 'secret echo of the request')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503, text })))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const err = await streamChat({ query: 'hi', sessionId: 's1' }, () => {}).catch((e) => e)
    expect(err).toBeInstanceOf(HttpStatusError)
    expect(err.status).toBe(503)
    expect(err.message).not.toContain('secret')
    expect(text).not.toHaveBeenCalled()
    expect(JSON.stringify(warn.mock.calls)).not.toContain('secret')
    warn.mockRestore()
  })

  it('delivers the error event, then done, with its payload intact', async () => {
    const got: Array<[string, unknown]> = []
    fakeResponse([
      block('error', { code: 'provider_billing', retryable: true, request_id: 'r1' }),
      block('done', {}),
    ])
    await streamChat({ query: 'hi', sessionId: 's1' }, (t, d) => got.push([t, d]))
    expect(got).toEqual([
      ['error', { code: 'provider_billing', retryable: true, request_id: 'r1' }],
      ['done', {}],
    ])
  })

  it('done with no token resolves and delivers no text (the caller decides no_answer)', async () => {
    fakeResponse([block('done', {})])
    const { events, p } = run()
    await expect(p).resolves.toBeUndefined()
    expect(events).toEqual(['done'])
  })

  it('a cut stream still delivers the text before it, then throws', async () => {
    const got: string[] = []
    fakeResponse([block('token', { content: 'half an ans' })], new Error('reset'))
    const p = streamChat({ query: 'hi', sessionId: 's1' }, (t, d) => {
      if (t === 'token') got.push((d as { content: string }).content)
    })
    await expect(p).rejects.toBeInstanceOf(StreamInterruptedError)
    expect(got.join('')).toBe('half an ans')
  })
})

describe('streamChat watchdog', () => {
  const silentResponse = (firstChunk?: string) => {
    let sent = false
    const cancel = vi.fn(async () => {})
    const make = () => ({
      read: () => {
        if (firstChunk && !sent) {
          sent = true
          return Promise.resolve({ done: false, value: enc.encode(firstChunk) })
        }
        return new Promise<never>(() => {}) // never answers
      },
      cancel,
    })
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, body: { getReader: make }, clone: () => ({ text: async () => '' }),
    })))
    return cancel
  }

  it('45 s without a byte rejects with StreamTimeoutError and cancels the body', async () => {
    vi.useFakeTimers()
    try {
      const cancel = silentResponse()
      const p = streamChat({ query: 'hi', sessionId: 's1' }, () => {})
      const assertion = expect(p).rejects.toBeInstanceOf(StreamTimeoutError)
      await vi.advanceTimersByTimeAsync(SSE_IDLE_TIMEOUT_MS - 1)
      expect(cancel).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(1)
      await assertion
      expect(cancel).toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('text delivered before the silence still reaches the caller', async () => {
    vi.useFakeTimers()
    try {
      silentResponse(block('token', { content: 'part' }))
      const events: string[] = []
      const p = streamChat({ query: 'hi', sessionId: 's1' }, (t) => events.push(t))
      const assertion = expect(p).rejects.toBeInstanceOf(StreamTimeoutError)
      await vi.advanceTimersByTimeAsync(SSE_IDLE_TIMEOUT_MS)
      await assertion
      expect(events).toEqual(['token'])
    } finally {
      vi.useRealTimers()
    }
  })

  it('any bytes, a ping comment included, restart the clock', async () => {
    vi.useFakeTimers()
    try {
      const queue: Array<(v: { done: boolean; value?: Uint8Array }) => void> = []
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        body: { getReader: () => ({ read: () => new Promise((r) => queue.push(r)), cancel: async () => {} }) },
        clone: () => ({ text: async () => '' }),
      })))
      const events: string[] = []
      const p = streamChat({ query: 'hi', sessionId: 's1' }, (t) => events.push(t))
      await vi.advanceTimersByTimeAsync(0)
      await vi.advanceTimersByTimeAsync(SSE_IDLE_TIMEOUT_MS - 1_000)
      queue.shift()!({ done: false, value: enc.encode(': ping\r\n\r\n') })
      await vi.advanceTimersByTimeAsync(SSE_IDLE_TIMEOUT_MS - 1_000) // 2x the limit in total, no timeout
      queue.shift()!({ done: false, value: enc.encode(block('done', {})) })
      await vi.advanceTimersByTimeAsync(0)
      queue.shift()?.({ done: true })
      await vi.advanceTimersByTimeAsync(0)
      await expect(p).resolves.toBeUndefined()
      expect(events).toEqual(['done'])
    } finally {
      vi.useRealTimers()
    }
  })
})
