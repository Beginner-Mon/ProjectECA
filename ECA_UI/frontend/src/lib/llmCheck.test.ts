import { describe, expect, it, beforeEach, vi, afterEach } from 'vitest'
import { checkConnection, listModels, type CheckConn } from './llmCheck'

const BASE = 'https://provider.example/v1'
const KEY = 'k-12345678'

function conn(over: Partial<CheckConn> = {}): CheckConn {
  return { baseUrl: BASE, apiKey: KEY, ...over }
}

function sseBody(text: string): string {
  // Split "pong" across two data lines to prove joining works.
  void text
  return (
    'data: {"choices":[{"delta":{"content":"po"}}]}\n\n' +
    'data: {"choices":[{"delta":{"content":"ng"}}]}\n\n' +
    'data: [DONE]\n\n'
  )
}

function sseResponse(text = 'pong'): Response {
  return new Response(sseBody(text), {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

function chunkedSseResponse(chunks: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk))
      controller.close()
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

const KNOWN_CATALOG = {
  testp: {
    models: {
      'known-model': {
        modalities: { input: ['text', 'image'], output: ['text'] },
        tool_call: true,
        temperature: true,
        cost: { input: 1 },
        release_date: '2025-01-01',
      },
    },
  },
}

let calls: Array<{ url: string; init: RequestInit; body: Record<string, unknown> | null }>

function mockFetch(handler: (url: string, init: RequestInit) => Response | Promise<Response> | never) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit) => {
      const body: Record<string, unknown> | null = (() => {
        try {
          return init?.body ? (JSON.parse(init.body as string) as Record<string, unknown>) : null
        } catch {
          return null
        }
      })()
      calls.push({ url, init, body })
      return handler(url, init)
    }),
  )
}

function chatCalls(): Array<{ url: string; body: Record<string, unknown> | null }> {
  return calls.filter((c) => c.url.endsWith('/chat/completions'))
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('error mapping', () => {
  const cases: Array<[number, string]> = [
    [401, 'auth'],
    [403, 'auth'],
    [402, 'quota'],
    [429, 'quota'],
    [404, 'model_not_found'],
    [400, 'bad_request'],
    [422, 'bad_request'],
    [500, 'server'],
  ]
  for (const [status, kind] of cases) {
    it(`maps ping ${status} to ${kind}`, async () => {
      mockFetch((url) => {
        if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'known-model' }] })
        return new Response('e', { status })
      })
      const res = await checkConnection(conn({ catalogId: 'testp' }), 'known-model', KNOWN_CATALOG)
      expect(res.ok).toBe(false)
      expect(res.error?.kind).toBe(kind)
    })
  }

  it('maps a throwing fetch to network', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'm' }] })
      throw new TypeError('fetch failed')
    })
    const res = await checkConnection(conn(), 'm', null)
    expect(res.ok).toBe(false)
    expect(res.error?.kind).toBe('network')
  })

  it('stops on listModels auth without any probe', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models')) return new Response('no', { status: 401 })
      return sseResponse()
    })
    const res = await checkConnection(conn(), 'm', null)
    expect(res.ok).toBe(false)
    expect(res.error?.kind).toBe('auth')
    expect(chatCalls()).toHaveLength(0)
  })
})

describe('call budget (metadata first)', () => {
  it('lists only when no model is given, with a suggestion', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models'))
        return jsonResponse({ data: [{ id: 'known-model' }, { id: 'other' }] })
      return sseResponse()
    })
    const res = await checkConnection(conn({ catalogId: 'testp' }), undefined, KNOWN_CATALOG)
    expect(chatCalls()).toHaveLength(0)
    expect(res.suggested).toBe('known-model')
    expect(res.models).toEqual(['known-model', 'other'])
  })

  it('uses exactly one chat/completions call for a catalogued model', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'known-model' }] })
      return sseResponse()
    })
    const res = await checkConnection(conn({ catalogId: 'testp' }), 'known-model', KNOWN_CATALOG)
    expect(res.ok).toBe(true)
    expect(chatCalls()).toHaveLength(1)
    expect(res.capabilities).toEqual(expect.arrayContaining(['text', 'answer', 'tools', 'image']))
    expect(res.omit).toEqual([])
  })

  it('uses two calls (ping, tools) for an uncatalogued model', async () => {
    mockFetch((url, init) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'custom' }] })
      const body = JSON.parse(init.body as string) as Record<string, unknown>
      if ('tools' in body)
        return jsonResponse({ choices: [{ message: { tool_calls: [{ id: '1' }] } }] })
      return sseResponse()
    })
    const res = await checkConnection(conn(), 'custom', null)
    expect(res.ok).toBe(true)
    expect(chatCalls()).toHaveLength(2)
    expect(res.capabilities).toEqual(expect.arrayContaining(['text', 'answer', 'tools']))
    expect(res.capabilities).not.toContain('image')
  })

  it('treats 404/405 on listModels as unlisted, not a failure', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models')) return new Response('x', { status: 404 })
      return sseResponse()
    })
    const res = await checkConnection(conn(), 'custom', null)
    expect(res.modelsListed).toBe(false)
    expect(res.ok).toBe(true)
  })
})

describe('ping backoff', () => {
  it('drops temperature then max tokens, recording omit', async () => {
    mockFetch((url, init) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'known-model' }] })
      const body = JSON.parse(init.body as string) as Record<string, unknown>
      if ('temperature' in body) return new Response('bad', { status: 400 })
      if ('max_completion_tokens' in body) return new Response('bad', { status: 400 })
      return sseResponse()
    })
    const res = await checkConnection(conn({ catalogId: 'testp' }), 'known-model', KNOWN_CATALOG)
    expect(res.ok).toBe(true)
    expect(res.omit).toEqual(['temperature', 'max_tokens'])
    expect(res.capabilities).toEqual(expect.arrayContaining(['text', 'answer']))
  })

  it('keeps only text when just the system-user layout works', async () => {
    mockFetch((url, init) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'm' }] })
      const body = JSON.parse(init.body as string) as {
        temperature?: unknown
        max_completion_tokens?: unknown
        messages?: unknown[]
      }
      const minimal =
        !('temperature' in body) &&
        !('max_completion_tokens' in body) &&
        (body.messages?.length ?? 0) === 2
      if (!minimal) return new Response('bad', { status: 400 })
      return sseResponse()
    })
    const res = await checkConnection(conn(), 'm', null)
    expect(res.ok).toBe(true)
    expect(res.capabilities).toContain('text')
    expect(res.capabilities).not.toContain('answer')
  })

  it('fails with no_text on an empty stream', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'm' }] })
      if (url.endsWith('/chat/completions')) return new Response('data: [DONE]\n\n', {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      })
      return sseResponse()
    })
    const res = await checkConnection(conn(), 'm', KNOWN_CATALOG)
    expect(res.ok).toBe(false)
    expect(res.error?.kind).toBe('no_text')
  })

  it('joins a line split across two reads', async () => {
    const full = 'data: {"choices":[{"delta":{"content":"pong"}}]}\n\n' + 'data: [DONE]\n\n'
    const cut = 20
    mockFetch((url) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'm' }] })
      if (url.endsWith('/chat/completions'))
        return chunkedSseResponse([full.slice(0, cut), full.slice(cut)])
      return jsonResponse({ choices: [{ message: { content: 'x' } }] })
    })
    const res = await checkConnection(conn(), 'm', null)
    expect(res.ok).toBe(true)
  })
})

describe('a provider that will not take the trial message from a browser', () => {
  // Shaped like OpenCode: the model list is open to a web page, the chat
  // address is not, so the browser's fetch to it throws.
  function listingOnly(listing: (key: string) => Response) {
    mockFetch((url, init) => {
      if (!url.endsWith('/models')) throw new TypeError('Failed to fetch')
      const auth = ((init.headers ?? {}) as Record<string, string>).Authorization ?? ''
      return listing(auth.replace('Bearer ', ''))
    })
  }
  const LIST = { data: [{ id: 'known-model' }] }

  it('reports the key as accepted when the listing refuses a wrong key but took this one', async () => {
    listingOnly((key) => (key === KEY ? jsonResponse(LIST) : new Response('no', { status: 401 })))
    const res = await checkConnection(conn({ catalogId: 'testp' }), 'known-model', KNOWN_CATALOG)
    expect(res.ok).toBe(false)
    expect(res.error?.kind).toBe('network')
    expect(res.untested).toEqual({ keyAccepted: true })
  })

  it('cannot vouch for the key when the listing is open to anyone', async () => {
    listingOnly(() => jsonResponse(LIST))
    const res = await checkConnection(conn({ catalogId: 'testp' }), 'known-model', KNOWN_CATALOG)
    expect(res.untested).toEqual({ keyAccepted: false })
  })

  it('takes what the model can do from the catalog, since no trial could say', async () => {
    listingOnly(() => jsonResponse(LIST))
    const res = await checkConnection(conn({ catalogId: 'testp' }), 'known-model', KNOWN_CATALOG)
    expect(res.capabilities).toEqual(['text', 'answer', 'tools', 'image'])
    expect(res.omit).toEqual([])
  })

  it('is a plain failure, not "untested", when the listing never came back either', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models')) return new Response('x', { status: 404 })
      throw new TypeError('Failed to fetch')
    })
    const res = await checkConnection(conn(), 'custom', null)
    expect(res.error?.kind).toBe('network')
    expect(res.untested).toBeUndefined()
  })

  it('sends the made-up key to the listing only, never the real one twice over', async () => {
    listingOnly((key) => (key === KEY ? jsonResponse(LIST) : new Response('no', { status: 401 })))
    await checkConnection(conn({ catalogId: 'testp' }), 'known-model', KNOWN_CATALOG)
    const wrongKeyCalls = calls.filter(
      (c) => ((c.init.headers ?? {}) as Record<string, string>).Authorization !== `Bearer ${KEY}`,
    )
    expect(wrongKeyCalls).toHaveLength(1)
    expect(wrongKeyCalls[0].url).toBe(`${BASE}/models`)
  })
})

describe('key scope', () => {
  it('sends the key only to the connection baseUrl', async () => {
    mockFetch((url) => {
      if (url.endsWith('/models')) return jsonResponse({ data: [{ id: 'm' }] })
      return sseResponse()
    })
    await checkConnection(conn(), 'm', KNOWN_CATALOG)
    expect(calls.length).toBeGreaterThan(0)
    for (const call of calls) {
      expect(call.url.startsWith(BASE)).toBe(true)
      const headers = (call.init.headers ?? {}) as Record<string, string>
      expect(headers.Authorization).toBe(`Bearer ${KEY}`)
    }
  })

  it('honours preset checkHeaders', async () => {
    mockFetch(() => jsonResponse({ data: [] }))
    await listModels(conn({ checkHeaders: { 'x-custom': '1' } }))
    const headers = (calls[0].init.headers ?? {}) as Record<string, string>
    expect(headers['x-custom']).toBe('1')
  })
})
