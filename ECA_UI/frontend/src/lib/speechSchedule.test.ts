import { describe, expect, it, vi } from 'vitest'

import {
  ChunkScheduler,
  MAX_START_DELAY_S,
  START_SAFETY_S,
  computeStartTime,
  locate,
  mediaPositionAt,
  type ChunkArrival,
  type Segment,
} from './speechSchedule'

const S = START_SAFETY_S
const close = (actual: number, expected: number) => expect(actual).toBeCloseTo(expected, 9)

describe('ChunkScheduler', () => {
  it('butts on-time chunks end to end on the audio clock', () => {
    const s = new ChunkScheduler()
    const [a] = s.offer(0, 2.0, 10)
    const [b] = s.offer(1, 2.5, 10.4) // lands long before chunk 0 ends
    const [c] = s.offer(2, 1.0, 11)
    close(a.startAt, 10 + S)
    close(b.startAt, 10 + S + 2.0)
    close(c.startAt, 10 + S + 4.5)
    expect([a.lateBy, b.lateBy, c.lateBy]).toEqual([0, 0, 0])
  })

  it('holds decodes that finish out of order, then releases them in seq order', () => {
    // decodeAudioData settles in whatever order it likes. Playing chunks in
    // decode order would scramble the sentence.
    const s = new ChunkScheduler()
    expect(s.offer(2, 1.0, 5)).toEqual([])
    expect(s.offer(1, 3.0, 5)).toEqual([])
    const released = s.offer(0, 2.0, 5)

    expect(released.map((p) => p.seq)).toEqual([0, 1, 2])
    // Start times follow seq order and each chunk's duration, not decode order.
    close(released[0].startAt, 5 + S)
    close(released[1].startAt, 5 + S + 2.0)
    close(released[2].startAt, 5 + S + 5.0)
    expect(released.map((p) => p.mediaStart)).toEqual([0, 2, 5])
  })

  it('schedules a late chunk as soon as possible and reports the gap', () => {
    const s = new ChunkScheduler()
    s.offer(0, 1.0, 0) // sounds 0.05 → 1.05
    const [late] = s.offer(1, 1.0, 3.0) // its slot was 1.05; it is now 3.0

    close(late.startAt, 3.0 + S)
    close(late.lateBy, 3.0 + S - 1.05)

    // The timeline carries on from where the late chunk really ends — not from
    // the slot it missed, which would stack the next chunk on top of it.
    const [next] = s.offer(2, 1.0, 3.1)
    close(next.startAt, 3.0 + S + 1.0)
    expect(next.lateBy).toBe(0)
  })

  it('never calls the first chunk late — it defines the timeline', () => {
    const [first] = new ChunkScheduler().offer(0, 1.0, 1234)
    expect(first.lateBy).toBe(0)
    close(first.startAt, 1234 + S)
  })

  it('skips an undecodable chunk instead of stalling everything behind it', () => {
    const s = new ChunkScheduler()
    s.offer(0, 1.0, 0)
    expect(s.offer(2, 1.0, 0.2)).toEqual([]) // waiting on 1
    const out = s.offer(1, null, 0.3)

    expect(out.map((p) => p.seq)).toEqual([2])
    close(out[0].startAt, S + 1.0) // straight after chunk 0: the bad one takes no time
    expect(out[0].lateBy).toBe(0)
    expect(s.waitingFor).toBe(3)
  })

  it('ignores duplicates and chunks it has already passed', () => {
    const s = new ChunkScheduler()
    s.offer(0, 1.0, 0)
    expect(s.offer(0, 1.0, 0)).toEqual([])
    expect(s.offer(2, 1.0, 0)).toEqual([])
    expect(s.offer(2, 9.9, 0)).toEqual([]) // held already; the first offer stands
    const [one, two] = s.offer(1, 1.0, 0)
    close(one.startAt, S + 1.0)
    close(two.startAt, S + 2.0)
  })

  it('resumes mid-chunk: the first placement carries the offset, the rest do not', () => {
    const s = new ChunkScheduler({ seq: 1, offset: 0.25, mediaStart: 2.0 })
    expect(s.offer(0, 2.0, 7)).toEqual([]) // before the resume point — already heard

    const [first] = s.offer(1, 1.0, 7)
    expect(first.offset).toBe(0.25)
    close(first.length, 0.75)
    close(first.mediaStart, 2.25)
    close(first.startAt, 7 + S)

    const [second] = s.offer(2, 1.0, 7)
    expect(second.offset).toBe(0)
    close(second.startAt, 7 + S + 0.75)
    close(second.mediaStart, 3.0)
  })
})

describe('mediaPositionAt', () => {
  // Chunk 2 was late: silence between 4 and 5 on the context clock.
  const segments: Segment[] = [
    { startAt: 1, length: 2, mediaStart: 0 },
    { startAt: 3, length: 1, mediaStart: 2 },
    { startAt: 5, length: 1, mediaStart: 3 },
  ]

  it('reports where the run started before anything sounds', () => {
    expect(mediaPositionAt(segments, 0.5, 0)).toBe(0)
    expect(mediaPositionAt([], 99, 1.5)).toBe(1.5)
  })

  it('follows the clip clock inside a chunk', () => {
    close(mediaPositionAt(segments, 3.5, 0), 2.5)
  })

  it('holds still through a starvation gap, like the voice does', () => {
    close(mediaPositionAt(segments, 4.5, 0), 3)
  })

  it('stops at the end', () => {
    close(mediaPositionAt(segments, 99, 0), 4)
  })
})

describe('locate', () => {  it('finds the chunk, and the offset into it, for a clip time', () => {
    expect(locate([2, 3, 1], 2.5)).toEqual({ seq: 1, offset: 0.5, mediaStart: 2 })
  })

  it('starts at the top for 0, decoded or not', () => {
    expect(locate([2, 3], 0)).toEqual({ seq: 0, offset: 0, mediaStart: 0 })
    expect(locate([], 0)).toEqual({ seq: 0, offset: 0, mediaStart: 0 })
  })

  it('stops at the first chunk whose duration is not known yet', () => {
    expect(locate([2, undefined, 1], 4)).toEqual({ seq: 1, offset: 0, mediaStart: 2 })
  })

  it('gives undecodable chunks no time, as the scheduler does', () => {
    expect(locate([2, null, 1], 2.5)).toEqual({ seq: 2, offset: 0.5, mediaStart: 2 })
  })

  it('lands past the last chunk when asked for the end', () => {
    expect(locate([2, 3], 5)).toEqual({ seq: 2, offset: 0, mediaStart: 5 })
  })
})

describe('computeStartTime', () => {
  // The measured shape (D6): 2.24s of audio every 2.90s — generation at
  // ~0.77× realtime, inside the 0.64–0.74× band that starves playback.
  const D = 2.24
  const G = 2.9
  const N = 15
  const arrivals: ChunkArrival[] = Array.from({ length: N }, (_, k) => ({ at: k * G, duration: D }))
  const EST = N * D // 33.6s of audio

  function scheduleAll(firstStartAt: number | undefined): { lateBy: number[]; startAt: number } {
    const s = new ChunkScheduler(undefined, undefined, firstStartAt)
    const lateBy: number[] = []
    let first = 0
    for (let k = 0; k < N; k++) {
      for (const p of s.offer(k, D, k * G)) {
        if (p.seq === 0) first = p.startAt
        lateBy[p.seq] = p.lateBy
      }
    }
    return { lateBy, startAt: first }
  }

  it('the measured slow stream plays with no audible gap once buffered', () => {
    const now = arrivals[2].at
    const startAt = computeStartTime(arrivals.slice(0, 3), EST, now)
    // doneAt − est + one chunk: 40.6 − 33.6 + 2.24 = 9.24.
    close(startAt, 9.24)
    const { lateBy } = scheduleAll(startAt)
    // Nothing past the 50 ms safety quantum — an exact tie between a slot
    // and an arrival still costs one quantum by design, and that is
    // inaudible. Anything above it would be a real gap.
    expect(Math.max(...lateBy)).toBeLessThanOrEqual(START_SAFETY_S + 1e-9)
  })

  it('the same stream starves from the early chunks without the buffer', () => {
    const { lateBy } = scheduleAll(undefined)
    expect(lateBy[2]).toBeGreaterThan(0.3)
    expect(Math.max(...lateBy)).toBeGreaterThan(0.5)
  })

  it('a faster-than-realtime stream starts at once, never early-waits', () => {
    const fast: ChunkArrival[] = [0, 1, 2].map((k) => ({ at: k * 1.5, duration: D }))
    close(computeStartTime(fast, EST, fast[2].at), fast[2].at + S)
  })

  it('a stream that ends before three chunks plays at once', () => {
    const two = arrivals.slice(0, 2)
    close(computeStartTime(two, EST, two[1].at), two[1].at + S)
    close(computeStartTime([], EST, 10), 10 + S)
  })

  it('an unmeasurable rate falls back to starting at once, never throws', () => {
    const tied: ChunkArrival[] = [
      { at: 5, duration: 2 },
      { at: 5, duration: 2 },
      { at: 5, duration: 2 },
    ]
    close(computeStartTime(tied, EST, 5), 5 + S)
    const silent: ChunkArrival[] = [
      { at: 0, duration: 0 },
      { at: 1, duration: 0 },
      { at: 2, duration: 0 },
    ]
    close(computeStartTime(silent, EST, 2), 2 + S)
  })

  it('an old server without estimated_audio_s still plays, guessing the total', () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})
    try {
      const startAt = computeStartTime(arrivals.slice(0, 3), undefined, arrivals[2].at)
      expect(Number.isFinite(startAt)).toBe(true)
      expect(debug).toHaveBeenCalled()
    } finally {
      debug.mockRestore()
    }
  })

  it('caps the wait at 15s and says so', () => {
    const debug = vi.spyOn(console, 'debug').mockImplementation(() => {})
    try {
      const now = arrivals[2].at
      // A D6-sized turn at the slow end of the band needs ~60s of buffer.
      const startAt = computeStartTime(arrivals.slice(0, 3), 126, now)
      close(startAt, now + MAX_START_DELAY_S)
      expect(debug).toHaveBeenCalled()
    } finally {
      debug.mockRestore()
    }
  })
})
