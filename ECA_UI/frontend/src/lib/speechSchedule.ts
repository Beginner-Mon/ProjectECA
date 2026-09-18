/**
 * Timing for gapless playback of streamed speech — the pure half of
 * speechPlayer.ts, kept free of Web Audio so the arithmetic can be tested under
 * vitest's `node` environment.
 *
 * The shape of the problem:
 *   - speech arrives as separate files (one self-standing OGG/Opus or WAV per
 *     `speech_chunk`), each decoded on its own by `decodeAudioData`;
 *   - decoding is async and does NOT finish in the order it was started — a
 *     short chunk queued second can beat a long one queued first;
 *   - Web Audio can start a buffer at an absolute time on the context clock.
 *
 * So the player decodes as fast as chunks arrive and hands each result here.
 * ChunkScheduler releases them strictly in `seq` order and stamps each with the
 * instant the previous one ends. Butting buffers end-to-end on the audio clock
 * is what makes the joins inaudible. Chaining on `onended` or a timer would put
 * a main-thread hop — a few ms of jitter, more under load — at every join,
 * and with ~25 joins per answer that is a stutter you can hear.
 */

/**
 * Lead time given to anything scheduled "now".
 *
 * A buffer started at exactly `currentTime` may already be behind the audio
 * thread, which renders a little ahead of what `currentTime` reports. 50 ms is
 * inaudible as start-up latency and comfortably clears that. It is only paid
 * by the first chunk of a run and by a chunk that arrives late — an on-time
 * chunk goes at its slot, which is already in the future.
 */
export const START_SAFETY_S = 0.05

/** Where a run begins: the first chunk to play and how far into it. */
export interface SchedulerStart {
  seq: number
  /** Seconds into chunk `seq`. Non-zero only when resuming or seeking. */
  offset: number
  /** Clip time at which chunk `seq` begins — the sum of the durations before it. */
  mediaStart: number
}

/** One stretch of the clip's timeline laid onto the context clock. */
export interface Segment {
  /** Context time it starts sounding. */
  startAt: number
  /** How long it sounds, in seconds. */
  length: number
  /** Clip time at which it starts. */
  mediaStart: number
}

/** A chunk ready to hand to `source.start(startAt, offset)`. */
export interface Placement extends Segment {
  seq: number
  /** Seconds into the buffer to begin from. Non-zero only for the first chunk
   *  of a resumed or seeked run. */
  offset: number
  /**
   * The audible gap in front of this chunk, in seconds: how far past its slot
   * it was when it became playable. 0 when on time.
   *
   * This is the metric that says streaming is starving. Generation runs ~2.2x
   * faster than playback, so every chunk should arrive seconds early; a
   * non-zero value means something upstream (a proxy buffering, a slow CPU,
   * the API Gateway) is holding chunks back.
   */
  lateBy: number
}

const FROM_START: SchedulerStart = { seq: 0, offset: 0, mediaStart: 0 }

export class ChunkScheduler {
  private nextSeq: number
  /** Context time the last placed chunk ends. Null until one has been placed —
   *  the first placement defines the timeline and so can never be late. */
  private nextStartTime: number | null = null
  /** Clip time at which chunk `nextSeq` begins. */
  private mediaCursor: number
  /** Applies to the first chunk placed, then resets to 0. */
  private firstOffset: number
  private readonly safety: number
  /** Decoded (duration) or failed (null) chunks waiting for an earlier seq. */
  private readonly held = new Map<number, number | null>()

  constructor(start: SchedulerStart = FROM_START, safety = START_SAFETY_S) {
    this.nextSeq = start.seq
    this.firstOffset = start.offset
    this.mediaCursor = start.mediaStart
    this.safety = safety
  }

  /** The seq everything is waiting on. Every seq below it has been placed or skipped. */
  get waitingFor(): number {
    return this.nextSeq
  }

  /**
   * A chunk finished decoding.
   *
   * @param duration seconds, or null when it could not be decoded. A null is
   *        skipped rather than waited on, so one bad chunk cannot stall the
   *        rest of the answer behind it. It contributes no time: the next chunk
   *        plays straight after the previous good one.
   * @param now      the context's `currentTime`.
   * @returns every chunk that can be placed now, in seq order — none if an
   *          earlier seq is still decoding.
   */
  offer(seq: number, duration: number | null, now: number): Placement[] {
    // Before the start of this run (a resume skips what was already heard), or
    // offered twice. Both are no-ops rather than errors: the player offers
    // everything it has decoded and lets this decide.
    if (seq < this.nextSeq || this.held.has(seq)) return []
    this.held.set(seq, duration)

    const out: Placement[] = []
    while (this.held.has(this.nextSeq)) {
      const d = this.held.get(this.nextSeq) ?? null
      this.held.delete(this.nextSeq)
      const current = this.nextSeq++
      if (d === null) continue

      const offset = this.firstOffset
      this.firstOffset = 0
      const length = d - offset
      if (length <= 0) {
        // Resume point at (or past) the very end of this chunk: nothing of it
        // is left to hear, but its time still counts on the clip's timeline.
        this.mediaCursor += d
        continue
      }

      let startAt: number
      let lateBy = 0
      if (this.nextStartTime === null) {
        startAt = now + this.safety
      } else {
        // The whole rule: its slot if the slot is still ahead of us, otherwise
        // as soon as possible — and the difference is the gap the user hears.
        startAt = Math.max(now + this.safety, this.nextStartTime)
        lateBy = startAt - this.nextStartTime
      }

      out.push({ seq: current, startAt, offset, length, mediaStart: this.mediaCursor + offset, lateBy })
      // The timeline continues from where this chunk really ends. After a late
      // chunk that is later than the missed slot; pretending otherwise would
      // schedule the next chunk on top of this one.
      this.nextStartTime = startAt + length
      this.mediaCursor += d
    }
    return out
  }
}

/**
 * Where in the clip playback is at context time `now`.
 *
 * @param segments placements in the order they were made (startAt ascending)
 * @param from     what to report before the first segment sounds — the clip
 *                 time the run started from
 *
 * During a starvation gap it holds at the end of the last chunk heard, which is
 * what the user actually heard; the progress bar pauses with the voice.
 */
export function mediaPositionAt(segments: readonly Segment[], now: number, from: number): number {
  let pos = from
  for (const s of segments) {
    if (s.startAt > now) break
    pos = s.mediaStart + Math.min(now - s.startAt, s.length)
  }
  return pos
}

/**
 * Find the chunk containing clip time `target`.
 *
 * @param durations per seq: seconds once decoded, null if undecodable (takes no
 *        time, as in the scheduler), undefined while not decoded yet.
 *
 * Stops at the first chunk not decoded yet: its duration is unknown, so nothing
 * after it can be located. That chunk is where the run starts, and it plays the
 * moment its decode lands.
 */
export function locate(durations: readonly (number | null | undefined)[], target: number): SchedulerStart {
  let mediaStart = 0
  for (let seq = 0; seq < durations.length; seq++) {
    const d = durations[seq]
    if (d === undefined) return { seq, offset: 0, mediaStart }
    if (d === null) continue
    if (target < mediaStart + d) return { seq, offset: Math.max(0, target - mediaStart), mediaStart }
    mediaStart += d
  }
  return { seq: durations.length, offset: 0, mediaStart }
}
