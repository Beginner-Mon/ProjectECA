import { afterEach, describe, expect, it, vi } from 'vitest'

import { playStaticAudio } from './characters'

/*
 * playStaticAudio only (finding 7): does aborting a signal actually stop
 * playback, not just cancel the network lookup that precedes it.
 *
 * The suite runs in vitest's `node` environment (see vitest.config.ts), so
 * `Audio` and `fetch` are not real browser globals — they are stubbed here
 * with the minimum surface playStaticAudio touches. That keeps this file
 * off jsdom (no DOM behaviour is under test, just event wiring).
 */

type Listener = () => void

class FakeAudio {
  src: string
  crossOrigin: string | null = null
  paused = true
  loaded = false
  private listeners: Record<string, Listener[]> = {}

  constructor(src: string) {
    this.src = src
  }

  addEventListener(type: string, cb: Listener) {
    ;(this.listeners[type] ??= []).push(cb)
  }

  removeEventListener(type: string, cb: Listener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((l) => l !== cb)
  }

  play = vi.fn(async () => {
    this.paused = false
  })

  pause = vi.fn(() => {
    this.paused = true
  })

  load = vi.fn(() => {
    this.loaded = true
  })

  removeAttribute = vi.fn((name: string) => {
    if (name === 'src') this.src = ''
  })

  dispatch(type: string) {
    for (const cb of this.listeners[type] ?? []) cb()
  }
}

function stubCharacterFetch(slug: string, url: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const href = typeof input === 'string' ? input : input.toString()
      if (init?.method === 'HEAD') {
        return new Response(null, { status: 200 })
      }
      if (href.includes(`/characters/${slug}`)) {
        return new Response(
          JSON.stringify({
            slug,
            display_name: slug,
            description: null,
            thumbnail_url: null,
            vrm_metadata: null,
            vrm_url: 'https://cdn.example/model.vrm',
            voice_language: 'vi',
            sort_order: 0,
            static_audio: { greeting: { vi: url } },
          }),
          { status: 200 },
        )
      }
      return new Response(null, { status: 404 })
    }),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('playStaticAudio — abort wiring (finding 7)', () => {
  it('aborting the signal mid-playback pauses and releases the element', async () => {
    stubCharacterFetch('anne', 'https://cdn.example/anne-greeting.ogg')
    let created: FakeAudio | null = null
    vi.stubGlobal(
      'Audio',
      vi.fn(function (src: string) {
        created = new FakeAudio(src)
        return created
      }),
    )

    const controller = new AbortController()
    const audio = (await playStaticAudio('anne', 'greeting', 'vi', controller.signal)) as unknown as FakeAudio
    expect(audio).toBe(created)
    expect(audio.paused).toBe(false) // play() resolved before abort

    controller.abort()

    expect(audio.pause).toHaveBeenCalled()
    expect(audio.paused).toBe(true)
    expect(audio.src).toBe('') // released, so a lingering reference can't keep fetching
  })

  it('does not start playback at all when the signal is already aborted', async () => {
    stubCharacterFetch('anne', 'https://cdn.example/anne-greeting.ogg')
    const audioCtor = vi.fn()
    vi.stubGlobal('Audio', audioCtor)

    const controller = new AbortController()
    controller.abort()
    const result = await playStaticAudio('anne', 'greeting', 'vi', controller.signal)

    expect(result).toBeNull()
    expect(audioCtor).not.toHaveBeenCalled()
  })

  it('an error-triggered retry bails out once the signal is aborted, instead of restarting playback', async () => {
    stubCharacterFetch('anne', 'https://cdn.example/anne-greeting.ogg')
    let created: FakeAudio | null = null
    vi.stubGlobal(
      'Audio',
      vi.fn(function (src: string) {
        created = new FakeAudio(src)
        return created
      }),
    )

    const controller = new AbortController()
    const audio = (await playStaticAudio('anne', 'greeting', 'vi', controller.signal)) as unknown as FakeAudio

    controller.abort()
    const playCallsBeforeError = audio.play.mock.calls.length
    audio.dispatch('error')
    await Promise.resolve()
    await Promise.resolve()

    // No retry fetch/play was attempted once aborted.
    expect(audio.play.mock.calls.length).toBe(playCallsBeforeError)
  })

  it('two rapid calls (abort-then-restart, as the greeting effect does) never leave two elements playing', async () => {
    stubCharacterFetch('anne', 'https://cdn.example/anne-greeting.ogg')
    const instances: FakeAudio[] = []
    vi.stubGlobal(
      'Audio',
      vi.fn(function (src: string) {
        const a = new FakeAudio(src)
        instances.push(a)
        return a
      }),
    )

    const controllerA = new AbortController()
    const audioA = (await playStaticAudio('anne', 'greeting', 'vi', controllerA.signal)) as unknown as FakeAudio

    // Caller switches avatar: cleanup aborts the first controller, then a
    // fresh effect run starts a second playback — the same order React
    // effect cleanup/re-run uses.
    controllerA.abort()

    stubCharacterFetch('miki', 'https://cdn.example/miki-greeting.ogg')
    const controllerB = new AbortController()
    const audioB = (await playStaticAudio('miki', 'greeting', 'vi', controllerB.signal)) as unknown as FakeAudio

    expect(instances.length).toBe(2)
    expect(audioA.paused).toBe(true)
    expect(audioB.paused).toBe(false)
  })
})
