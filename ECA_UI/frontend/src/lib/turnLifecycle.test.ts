import { describe, expect, it, vi } from 'vitest'

import { SpeechClip, applySpeechEvent } from './speechPlayer'
import { createTurnLifecycle } from './turnLifecycle'

/*
 * The real handler ChatContext runs, driven with fake UI hooks and a real
 * SpeechClip. "Speech still plays" is asserted as: autoplay was requested, and
 * every chunk after the release still reached the clip the player reads from.
 * The audio itself needs a browser's AudioContext and is not exercised here.
 */

function harness(opts: { voice?: boolean } = {}) {
  const ui = { generating: true, current: true, releases: 0, dirtyMarks: 0 }
  const speech = opts.voice === false ? undefined : new SpeechClip()
  const played: SpeechClip[] = []
  const handle = createTurnLifecycle(speech, {
    isCurrent: () => ui.current,
    releaseComposing: () => {
      ui.generating = false
      ui.releases++
    },
    markSessionsDirty: () => {
      ui.dirtyMarks++
    },
    routeSpeech: (clip, type, data) => {
      applySpeechEvent(clip, type, data)
    },
    playSpeech: (clip) => {
      played.push(clip)
    },
  })
  return { ui, speech, played, handle }
}

const START = { voice_version: 'abc', codec: 'opus', sample_rate: 48000 }
const chunk = (seq: number) => ({ seq, codec: 'opus', audio: btoa(`chunk-${seq}`) })

describe('turn lifecycle', () => {
  it('tokens → session_persisted → speech_start → chunks → speech_end → done', () => {
    const { ui, speech, played, handle } = harness()

    // Tokens are ChatContext's own business, not this handler's.
    expect(handle('token', { content: 'Xin chào' })).toBe(false)
    expect(ui.generating).toBe(true)

    handle('session_persisted', { session_id: 's1' })
    // Released the moment the turn is saved — before any audio exists.
    expect(ui.generating).toBe(false)
    expect(ui.releases).toBe(1)
    expect(ui.dirtyMarks).toBe(1)
    // And the release left the speech alone: not failed, not aborted.
    expect(speech!.status).toBe('pending')

    handle('speech_start', START)
    expect(played).toEqual([speech])
    expect(speech!.status).toBe('streaming')

    handle('speech_chunk', chunk(0))
    handle('speech_chunk', chunk(1))
    handle('speech_end', { chunks: 2 })
    expect(speech!.status).toBe('complete')
    expect(speech!.intact).toBe(true)
    expect(speech!.chunks).toHaveLength(2)

    handle('done', {})
    expect(ui.generating).toBe(false)
    // Already marked at session_persisted; a second mark is a second fetch.
    expect(ui.dirtyMarks).toBe(1)
  })

  it('keeps voicing a reply after the user has moved on to the next message', () => {
    const { ui, speech, played, handle } = harness()
    handle('session_persisted', { session_id: 's1' })
    // The user sends again: this stream is no longer the newest.
    ui.current = false
    ui.generating = true // the NEW turn is composing

    handle('speech_start', START)
    handle('speech_chunk', chunk(0))
    handle('speech_end', { chunks: 1 })
    handle('done', {})

    expect(played).toEqual([speech])
    expect(speech!.status).toBe('complete')
    // Nothing here may hand back the newer turn's composer.
    expect(ui.generating).toBe(true)
    expect(ui.releases).toBe(1)
  })

  it('falls back to speech_start, then marks the list at done, when persistence failed', () => {
    const { ui, handle } = harness()
    handle('speech_start', START)
    expect(ui.generating).toBe(false)
    expect(ui.dirtyMarks).toBe(0)

    handle('speech_end', { chunks: 0 })
    handle('done', {})
    expect(ui.dirtyMarks).toBe(1)
  })

  it('falls back to done for a text-only turn with no session_persisted', () => {
    const { ui, handle } = harness({ voice: false })
    handle('done', {})
    expect(ui.generating).toBe(false)
    expect(ui.dirtyMarks).toBe(1)
  })

  it('with voice off, speech events are swallowed and nothing plays', () => {
    const { ui, played, handle } = harness({ voice: false })
    handle('session_persisted', { session_id: 's1' })
    expect(handle('speech_start', START)).toBe(true)
    expect(played).toEqual([])
    expect(ui.releases).toBe(1)
  })

  it('ignores the events it does not own', () => {
    const { handle } = harness()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    for (const type of ['stage', 'token', 'motion']) expect(handle(type, {})).toBe(false)
    warn.mockRestore()
  })
})
