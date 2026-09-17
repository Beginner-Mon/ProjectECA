import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getGreetingForSlot, getTimeSlot, uiStringsFor, type TimeSlot } from './characterCopy'
import { buildGreetingKey, greetingCacheKey, playGreeting } from './greetingAudio'
import { readCachedClip, sha256Hex, writeCachedClip } from './ttsCache'
import { speechPlayer, type LipSyncTarget } from './speechPlayer'
import type { Character } from './characters'

/*
 * Greeting orchestration: cache-hit short-circuit, text_sha256 refusal,
 * per-entry TTL survival (covered in ttsCache.test.ts), abort and overlap,
 * and the lip-sync handoff.
 *
 * Two seams are faked. Web Audio cannot exist in vitest's `node`
 * environment, so `../avatar/lipSyncAudio` is a stub context whose
 * decodeAudioData resolves a 0.5s buffer — enough for the real
 * speechPlayer to schedule one chunk and call startLipSync. IndexedDB
 * cannot exist either, so the ttsCache READ/WRITE pair is an in-memory
 * Map while every pure decision (sha256Hex, TTL, eviction) stays real.
 */

vi.mock('../avatar/lipSyncAudio', () => {
  const makeSource = () => ({
    buffer: null as unknown,
    connect: vi.fn(),
    disconnect: vi.fn(),
    start: vi.fn(),
    stop: vi.fn(),
    onended: null as unknown,
  })
  const ctx = {
    state: 'running',
    currentTime: 0,
    destination: {},
    resume: vi.fn(async () => {}),
    decodeAudioData: vi.fn(async () => ({ duration: 0.5 })),
    createBufferSource: vi.fn(() => makeSource()),
    createAnalyser: vi.fn(() => ({ fftSize: 0, connect: vi.fn() })),
  }
  return {
    ensureAudioContext: vi.fn(() => ctx),
    createSpeechAnalyser: vi.fn(() => ({ fftSize: 0, connect: vi.fn() })),
  }
})

vi.mock('./api', () => ({
  authHeader: vi.fn(async () => ({ Authorization: 'Bearer test-token' })),
  cognitoSub: vi.fn(async () => 'user-test'),
}))

vi.mock('./ttsCache', async (importOriginal) => {
  const mod = await importOriginal<typeof import('./ttsCache')>()
  const store = new Map<string, { meta: never; chunks: ArrayBuffer[] }>()
  return {
    ...mod,
    readCachedClip: vi.fn(async (sub: string, key: string) => {
      const row = store.get(`${sub}:${key}`)
      return row ? { meta: row.meta, chunks: [...row.chunks] } : null
    }),
    writeCachedClip: vi.fn(async (sub: string, key: string, entry: never) => {
      store.set(`${sub}:${key}`, {
        meta: { id: `${sub}:${key}` } as never,
        chunks: [...(entry as { chunks: ArrayBuffer[] }).chunks],
      })
    }),
    __store: store,
  }
})

const { __store: cacheStore } = (await import('./ttsCache')) as unknown as {
  __store: Map<string, { meta: never; chunks: ArrayBuffer[] }>
}

const VI_GREETING = {
  morning: 'Chào buổi sáng! Hôm nay mình giúp gì cho bạn?',
  afternoon: 'Chào buổi chiều! Bạn đang thế nào rồi?',
  evening: 'Chào buổi tối! Mình ở đây với bạn.',
  night: 'Khuya rồi, bạn vẫn chưa ngủ à? Mình ở đây nhé.',
}
const EN_GREETING = {
  morning: 'Good morning! What can I help you with today?',
  afternoon: 'Good afternoon! How are you doing?',
  evening: 'Good evening! I am here with you.',
  night: 'Still up at this hour? I am here if you need me.',
}

function character(): Character {
  return {
    slug: 'anne',
    display_name: 'Anne',
    description: null,
    thumbnail_url: null,
    vrm_metadata: null,
    vrm_url: 'https://cdn.example/anne.vrm',
    voice_language: 'vi',
    sort_order: 0,
    ui_strings: { vi: { greeting: VI_GREETING }, en: { greeting: EN_GREETING } },
    audio_version: 'a1b2c3d4e5f6',
  } as unknown as Character
}

function mouth(): LipSyncTarget & { started: () => number; stopped: () => number } {
  const start = vi.fn()
  const stop = vi.fn()
  return {
    startLipSync: start,
    stopLipSync: stop,
    started: () => start.mock.calls.length,
    stopped: () => stop.mock.calls.length,
  }
}

/** Displayed text and its hash, via the same two functions the player uses. */
async function displayedTextHash(locale: 'vi' | 'en' = 'vi', slot: TimeSlot = 'morning') {
  const ui = uiStringsFor(character(), locale)
  const text = getGreetingForSlot(ui, slot)
  const hash = await sha256Hex(text)
  return { slot, text, hash: hash as string }
}

function stubAudioBackend(entry: Record<string, unknown>, bytes = new Uint8Array([1, 2, 3, 4]).buffer as ArrayBuffer) {
  return vi.stubGlobal(
    'fetch',
    vi.fn(async (input: unknown) => {
      const href = String(input)
      if (href.includes('/audio?')) {
        return new Response(JSON.stringify({ clips: entry }), { status: 200 })
      }
      return new Response(bytes, { status: 200 })
    }),
  )
}

beforeEach(() => {
  cacheStore.clear()
  speechPlayer.stop()
  // Module-level mocks keep call history across tests; restoreAllMocks does
  // not clear vi.fn() instances from vi.mock factories.
  vi.mocked(readCachedClip).mockClear()
  vi.mocked(writeCachedClip).mockClear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  speechPlayer.stop()
})

describe('greetingCacheKey', () => {
  it('carries every input the bytes depend on', () => {
    expect(greetingCacheKey('anne', 'greeting.morning', 'vi', 'a1b2', 'h4sh')).toBe(
      'static:anne:greeting.morning:vi:a1b2:h4sh',
    )
  })
})

describe('buildGreetingKey (fix #1)', () => {
  it('is identical across catalog reloads with fresh object identities', () => {
    // ensureCatalogLoaded rebuilds the array: same content, new references.
    // The old effect depended on the array and replayed Anne's greeting.
    const before = { slug: 'anne', audio_version: 'a1b2c3d4e5f6' }
    const after = { slug: 'anne', audio_version: 'a1b2c3d4e5f6' }
    expect(before).not.toBe(after)
    expect(buildGreetingKey(after, 'vi', 'morning')).toBe(buildGreetingKey(before, 'vi', 'morning'))
  })

  it('changes on avatar, language, slot, or re-render — null without a slug', () => {
    const base = { slug: 'anne', audio_version: 'a1b2c3d4e5f6' }
    const key = buildGreetingKey(base, 'vi', 'morning')
    expect(buildGreetingKey({ slug: 'miki', audio_version: 'a1b2c3d4e5f6' }, 'vi', 'morning')).not.toBe(key)
    expect(buildGreetingKey(base, 'en', 'morning')).not.toBe(key)
    expect(buildGreetingKey(base, 'vi', 'evening')).not.toBe(key)
    expect(buildGreetingKey({ slug: 'anne', audio_version: 'zzzz' }, 'vi', 'morning')).not.toBe(key)
    expect(buildGreetingKey(null, 'vi', 'morning')).toBeNull()
  })
})

describe('playGreeting', () => {
  it('cache hit plays at once with zero network requests', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
    const { slot, text, hash } = await displayedTextHash('vi', 'morning')
    const key = greetingCacheKey('anne', `greeting.${slot}`, 'vi', 'a1b2c3d4e5f6', hash)
    cacheStore.set(`user-test:${key}`, {
      meta: { id: `user-test:${key}` } as never,
      chunks: [new Uint8Array([9, 9, 9]).buffer as ArrayBuffer],
    })
    const fetchSpy = vi.fn(async () => new Response(null, { status: 500 }))
    vi.stubGlobal('fetch', fetchSpy)
    const controller = mouth()

    await playGreeting({ character: character(), locale: 'vi', slot, text, controller })

    expect(fetchSpy).not.toHaveBeenCalled()
    expect(writeCachedClip).not.toHaveBeenCalled()
    expect(controller.started()).toBe(1)
    expect(speechPlayer.getSnapshot().status).toBe('playing')
  })

  it('miss fetches /audio, verifies the hash, caches, and lip-sycs', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
    const { slot, text, hash } = await displayedTextHash('vi', 'morning')
    const clip = `greeting.${slot}`
    stubAudioBackend({
      [clip]: {
        url: 'https://cdn.example/anne-audio.ogg',
        expires_at: '2026-09-17T10:05:00Z',
        sha256: 's'.repeat(64),
        text_sha256: hash,
      },
    })
    const controller = mouth()

    await playGreeting({ character: character(), locale: 'vi', slot, text, controller })

    expect(readCachedClip).toHaveBeenCalled()
    expect(writeCachedClip).toHaveBeenCalledTimes(1)
    const [, key, entry] = (writeCachedClip as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      string,
      { kind: string; ttlMs: number; chunks: ArrayBuffer[] },
    ]
    expect(key).toBe(greetingCacheKey('anne', clip, 'vi', 'a1b2c3d4e5f6', hash))
    expect(entry.kind).toBe('static')
    expect(entry.ttlMs).toBe(30 * 24 * 60 * 60 * 1000)
    expect(controller.started()).toBe(1)
    expect(text).toBe(getGreetingForSlot(uiStringsFor(character(), 'vi'), slot))
    expect(text.length).toBeGreaterThan(0)
  })

  it.each<TimeSlot>(['morning', 'afternoon', 'evening', 'night'])(
    'miss path works for all greeting slots: %s',
    async (slot) => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
      const { text, hash } = await displayedTextHash('vi', slot)
      const clip = `greeting.${slot}`
      cacheStore.clear()
      vi.mocked(writeCachedClip).mockClear()
      stubAudioBackend({
        [clip]: {
          url: 'https://cdn.example/anne-audio.ogg',
          expires_at: '2026-09-17T10:05:00Z',
          sha256: 's'.repeat(64),
          text_sha256: hash,
        },
      })
      const controller = mouth()

      await playGreeting({ character: character(), locale: 'vi', slot, text, controller })

      expect(writeCachedClip).toHaveBeenCalledTimes(1)
      expect(controller.started()).toBe(1)
      expect(text).toBe(getGreetingForSlot(uiStringsFor(character(), 'vi'), slot))
      expect(text.length).toBeGreaterThan(0)
    },
  )

  it('text_sha256 mismatch plays nothing and caches nothing', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
    const { slot, text } = await displayedTextHash('vi', 'morning')
    const clip = `greeting.${slot}`
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const bytesFetch = vi.fn(async () => new Response(new ArrayBuffer(4), { status: 200 }))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown) => {
        const href = String(input)
        if (href.includes('/audio?')) {
          return new Response(
            JSON.stringify({
              clips: {
                [clip]: {
                  url: 'https://cdn.example/stale.ogg',
                  expires_at: '2026-09-17T10:05:00Z',
                  sha256: 's'.repeat(64),
                  text_sha256: '0'.repeat(64), // rendered from other words
                },
              },
            }),
            { status: 200 },
          )
        }
        return bytesFetch(input)
      }),
    )
    const controller = mouth()

    await playGreeting({ character: character(), locale: 'vi', slot, text, controller })

    expect(warn).toHaveBeenCalledWith(expect.stringContaining(clip))
    expect(bytesFetch).not.toHaveBeenCalled()
    expect(writeCachedClip).not.toHaveBeenCalled()
    expect(controller.started()).toBe(0)
  })

  it('null audio_version or null clip stays text-only with no requests', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
    const { slot, text } = await displayedTextHash('vi', 'morning')
    const fetchSpy = vi.fn(async () => new Response(null, { status: 500 }))
    vi.stubGlobal('fetch', fetchSpy)
    const controller = mouth()

    await playGreeting({
      character: { ...character(), audio_version: null },
      locale: 'vi',
      slot,
      text,
      controller,
    })

    expect(fetchSpy).not.toHaveBeenCalled()
    expect(controller.started()).toBe(0)
  })

  it('abort stops a playing greeting (b97eeda2 behaviour kept)', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
    const { slot, text, hash } = await displayedTextHash('vi', 'morning')
    stubAudioBackend({
      [`greeting.${slot}`]: {
        url: 'https://cdn.example/anne-audio.ogg',
        expires_at: '2026-09-17T10:05:00Z',
        sha256: 's'.repeat(64),
        text_sha256: hash,
      },
    })
    const controller = mouth()
    const aborter = new AbortController()

    await playGreeting({ character: character(), locale: 'vi', slot, text, controller, signal: aborter.signal })
    expect(speechPlayer.getSnapshot().status).toBe('playing')

    aborter.abort()

    expect(speechPlayer.getSnapshot().status).toBe('idle')
  })

  it('two rapid calls never overlap — the second preempts the first', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-17T09:00:00Z'))
    const { slot, text, hash } = await displayedTextHash('vi', 'morning')
    stubAudioBackend({
      [`greeting.${slot}`]: {
        url: 'https://cdn.example/anne-audio.ogg',
        expires_at: '2026-09-17T10:05:00Z',
        sha256: 's'.repeat(64),
        text_sha256: hash,
      },
    })
    const first = mouth()
    const second = mouth()
    const abortFirst = new AbortController()

    await playGreeting({ character: character(), locale: 'vi', slot, text, controller: first, signal: abortFirst.signal })
    abortFirst.abort() // React cleanup before the re-run, as the effect does
    await playGreeting({ character: { ...character(), slug: 'miki' }, locale: 'vi', slot, text, controller: second })

    expect(first.started()).toBe(1)
    expect(second.started()).toBe(1)
    expect(speechPlayer.getSnapshot().status).toBe('playing')
  })
})
