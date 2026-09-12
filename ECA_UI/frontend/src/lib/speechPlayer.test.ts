import { afterEach, describe, expect, it, vi } from 'vitest'

import { CLIP_DISABLED, SpeechClip, applySpeechEvent } from './speechPlayer'

/*
 * The clip and the event parsing. The player itself needs a real AudioContext
 * (decodeAudioData, the audio clock) and is not exercised here; its timing
 * arithmetic is tested in speechSchedule.test.ts.
 */

const START = { voice_version: 'abc', codec: 'opus', sample_rate: 48000 }
const chunk = (seq: number, bytes: string) => ({ seq, codec: 'opus', audio: btoa(bytes) })

afterEach(() => {
  vi.restoreAllMocks()
})

describe('applySpeechEvent', () => {
  it('builds a clip from the contract events, in seq order whatever the arrival order', () => {
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_start', START)
    applySpeechEvent(clip, 'speech_chunk', chunk(1, 'BB'))
    applySpeechEvent(clip, 'speech_chunk', chunk(0, 'A'))
    applySpeechEvent(clip, 'speech_end', { chunks: 2 })

    expect(clip.status).toBe('complete')
    expect(clip.meta).toEqual({ voiceVersion: 'abc', codec: 'opus', sampleRate: 48000, lang: undefined })
    expect(clip.chunks.map((c) => c?.byteLength)).toEqual([1, 2])
    expect(clip.intact).toBe(true)
  })

  it('turns chunks that never arrived into holes at the end, so playback cannot wait on them', () => {
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_start', START)
    applySpeechEvent(clip, 'speech_chunk', chunk(0, 'A'))
    applySpeechEvent(clip, 'speech_end', { chunks: 3 })

    expect(clip.chunks.length).toBe(3)
    expect(clip.chunks[1]).toBeNull()
    expect(clip.chunks[2]).toBeNull()
    // A clip with a hole replays with a piece missing; it is not cached.
    expect(clip.intact).toBe(false)
  })

  it('records a chunk whose base64 is garbage as a hole', () => {
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_start', START)
    applySpeechEvent(clip, 'speech_chunk', { seq: 0, codec: 'opus', audio: '***' })
    expect(clip.chunks[0]).toBeNull()
    expect(clip.getSnapshot().received).toBe(1)
  })

  it('ignores a chunk with no usable seq', () => {
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_start', START)
    applySpeechEvent(clip, 'speech_chunk', { codec: 'opus', audio: btoa('A') })
    applySpeechEvent(clip, 'speech_chunk', chunk(1e9, 'A'))
    expect(clip.chunks.length).toBe(0)
  })

  it('treats codec as opaque: any value is kept as sent, and absence is not guessed', () => {
    // The server may move to FLAC or WAV by config; nothing here may need to change.
    const flac = new SpeechClip()
    applySpeechEvent(flac, 'speech_start', { ...START, codec: 'flac' })
    expect(flac.meta?.codec).toBe('flac')

    const unnamed = new SpeechClip()
    applySpeechEvent(unnamed, 'speech_start', { voice_version: 'abc', sample_rate: 48000 })
    expect(unnamed.meta?.codec).toBe('')
  })

  it('reads lang when the server sends it, though the contract does not require it', () => {
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_start', { ...START, lang: 'vi' })
    expect(clip.meta?.lang).toBe('vi')
  })

  it('leaves other events alone', () => {
    expect(applySpeechEvent(new SpeechClip(), 'token', { content: 'x' })).toBe(false)
  })

  it('fails loudly on speech_failed', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_start', START)
    applySpeechEvent(clip, 'speech_failed', { error: 'model crashed' })
    expect(clip.status).toBe('failed')
    expect(clip.error).toBe('model crashed')
    expect(warn).toHaveBeenCalled()
  })

  it('fails quietly on speech_disabled — a setting, not a fault', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_disabled', { reason: 'not configured' })
    expect(clip.status).toBe('failed')
    expect(clip.error).toBe(CLIP_DISABLED)
    expect(warn).not.toHaveBeenCalled()
  })
})

describe('SpeechClip', () => {
  it('stays complete if the stream fails after speech_end', () => {
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_start', START)
    applySpeechEvent(clip, 'speech_chunk', chunk(0, 'A'))
    applySpeechEvent(clip, 'speech_end', { chunks: 1 })
    clip.fail('connection reset')
    expect(clip.status).toBe('complete')
    expect(clip.intact).toBe(true)
  })

  it('accepts no chunks before speech_start', () => {
    const clip = new SpeechClip()
    applySpeechEvent(clip, 'speech_chunk', chunk(0, 'A'))
    expect(clip.chunks.length).toBe(0)
    expect(clip.status).toBe('pending')
  })

  it('hands subscribers a new snapshot object on every change', () => {
    // useSyncExternalStore compares snapshots by identity.
    const clip = new SpeechClip()
    const seen: unknown[] = []
    const unsubscribe = clip.subscribe(() => seen.push(clip.getSnapshot()))
    applySpeechEvent(clip, 'speech_start', START)
    applySpeechEvent(clip, 'speech_chunk', chunk(0, 'A'))
    unsubscribe()
    applySpeechEvent(clip, 'speech_end', { chunks: 1 })

    expect(seen).toHaveLength(2)
    expect(seen[0]).not.toBe(seen[1])
    expect(clip.getSnapshot()).toEqual({ status: 'complete', received: 1, error: null })
  })

  it('rebuilds a finished clip from the cache', () => {
    const clip = SpeechClip.fromCache(
      { voiceVersion: 'abc', codec: 'opus', sampleRate: 48000 },
      [new ArrayBuffer(3), new ArrayBuffer(4)],
    )
    expect(clip.status).toBe('complete')
    expect(clip.intact).toBe(true)
    expect(clip.getSnapshot().received).toBe(2)
  })
})
