/**
 * Streamed speech: the clip a reply's audio arrives into, and the one player
 * that speaks it.
 *
 * Replaces one <audio> element per message. That design could not stream — an
 * element plays a URL, and there is no URL until the whole answer is
 * synthesised — and it made every message its own player, so "only one voice at
 * a time" was a convention (a global speaker id in the old speechLipSync.ts)
 * rather than a fact.
 *
 * Now:
 *   - SpeechClip holds one reply's audio as it arrives: the encoded chunk files
 *     from `speech_chunk` events, in seq order. Plain data plus a subscription;
 *     no Web Audio, so it can live on a Message and be unit-tested.
 *   - speechPlayer (a singleton) decodes a clip's chunks and schedules them
 *     back to back on the AudioContext clock (lib/speechSchedule.ts), all
 *     through ONE AnalyserNode. That single analyser is what keeps the avatar's
 *     mouth moving continuously across chunk joins: lip sync reads one node for
 *     the whole answer, not a new one per chunk.
 *
 * Autoplay policy: a browser will not start audio without a user gesture. If
 * the context is not running when a clip should start, `play` returns false
 * and changes nothing — the clip keeps every chunk, and the speaker button
 * starts it on the next click. Audio is never dropped for want of a gesture.
 */

import { createSpeechAnalyser, ensureAudioContext } from '../avatar/lipSyncAudio'
import {
  ChunkScheduler,
  computeStartTime,
  locate,
  MAX_START_DELAY_S,
  priorStartTime,
  mediaPositionAt,
  type ChunkArrival,
  type Placement,
} from './speechSchedule'

// ── SpeechClip ───────────────────────────────────────────────────────────────

/** `pending` exists before `speech_start`, so voice mode can show "audio coming"
 *  from the moment the message is created. */
export type ClipStatus = 'pending' | 'streaming' | 'complete' | 'failed'

/** Why a clip failed, when that decides what the UI says. */
export const CLIP_ABORTED = 'aborted'
export const CLIP_DISABLED = 'disabled'
/** Set on a COMPLETE clip this device could not decode a single chunk of —
 *  most likely Ogg/Opus on an older iOS WebView. */
export const CLIP_UNDECODABLE = 'undecodable'

/** Far above any real answer (~25 chunks), low enough that a garbage `seq`
 *  cannot allocate a sparse array millions long. */
const MAX_CHUNKS = 4096

export interface SpeechMeta {
  voiceVersion: string
  /**
   * Opaque metadata: stored and logged, never branched on. Decoding does not
   * need it — `decodeAudioData` sniffs the container from the bytes — so the
   * server can move between Opus, FLAC and WAV (the Opus joins measured a few
   * audible-risk clicks) with no change here.
   */
  codec: string
  sampleRate: number
  /** Not part of the speech_start contract today; read if the server sends it.
   *  See `voiceScope` in ttsCache.ts for why it matters. */
  lang?: string
  /**
   * Expected total audio seconds (server's estimate from the spoken char
   * count). Not part of older servers' speech_start; read if sent, like
   * `lang` above. The player needs it BEFORE the stream ends to measure the
   * gapless buffer (computeStartTime) — by `speech_end` it would be useless.
   */
  estimatedAudioS?: number
}

export interface ClipSnapshot {
  status: ClipStatus
  /** Chunks that have arrived, usable or not. */
  received: number
  error: string | null
}

export class SpeechClip {
  meta: SpeechMeta | null = null
  /**
   * Encoded chunk files by seq: an ArrayBuffer once it has arrived; null if it
   * arrived unusable (bad base64) or never arrived before the end; undefined
   * while it is still expected.
   *
   * Kept encoded, not decoded. Opus speech is a few kB per second; the decoded
   * float PCM is ~190 kB per second. A conversation full of voiced replies
   * would otherwise hold tens of MB of audio nobody is playing. Decoding again
   * on replay costs milliseconds per chunk.
   */
  readonly chunks: (ArrayBuffer | null | undefined)[] = []
  private snap: ClipSnapshot = { status: 'pending', received: 0, error: null }
  private readonly listeners = new Set<() => void>()

  /** A finished clip rebuilt from the IndexedDB cache. */
  static fromCache(meta: SpeechMeta, chunks: readonly ArrayBuffer[]): SpeechClip {
    const clip = new SpeechClip()
    clip.meta = meta
    for (const c of chunks) clip.chunks.push(c)
    clip.snap = { status: 'complete', received: chunks.length, error: null }
    return clip
  }

  get status(): ClipStatus {
    return this.snap.status
  }

  get error(): string | null {
    return this.snap.error
  }

  /** Nothing more will arrive. */
  get settled(): boolean {
    return this.snap.status === 'complete' || this.snap.status === 'failed'
  }

  /**
   * Complete, with every chunk present — the only state worth caching. A clip
   * with a hole would replay with a piece of the sentence missing, every time,
   * for a day; better to miss and synthesise it whole.
   */
  get intact(): boolean {
    if (this.snap.status !== 'complete') return false
    // An index loop, not `every`: `every` skips the holes of a sparse array,
    // and holes are exactly what this is looking for.
    for (let i = 0; i < this.chunks.length; i++) {
      if (!(this.chunks[i] instanceof ArrayBuffer)) return false
    }
    return true
  }

  begin(meta: SpeechMeta): void {
    if (this.snap.status !== 'pending') return
    this.meta = meta
    this.set({ status: 'streaming' })
  }

  addChunk(seq: number, bytes: ArrayBuffer | null): void {
    if (this.snap.status !== 'streaming') return
    if (!Number.isInteger(seq) || seq < 0 || seq >= MAX_CHUNKS) return
    if (this.chunks[seq] !== undefined) return // duplicate
    this.chunks[seq] = bytes && bytes.byteLength > 0 ? bytes : null
    this.set({ received: this.snap.received + 1 })
  }

  /**
   * `speech_end`. Any seq below `count` that never arrived becomes a hole
   * (null), so the player skips it instead of waiting for it forever.
   */
  finish(count?: number): void {
    if (this.snap.status !== 'streaming') return
    const total = Math.min(MAX_CHUNKS, Math.max(count ?? 0, this.chunks.length))
    for (let i = 0; i < total; i++) {
      if (this.chunks[i] === undefined) this.chunks[i] = null
    }
    this.set({ status: 'complete' })
  }

  /** No-op once settled: a clip that arrived whole stays whole, whatever the
   *  stream does after `speech_end`. */
  fail(reason: string): void {
    if (this.settled) return
    this.set({ status: 'failed', error: reason })
  }

  markUndecodable(): void {
    if (this.snap.error === null) this.set({ error: CLIP_UNDECODABLE })
  }

  // Arrow properties so they can be handed straight to useSyncExternalStore.
  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  getSnapshot = (): ClipSnapshot => this.snap

  private set(patch: Partial<ClipSnapshot>): void {
    this.snap = { ...this.snap, ...patch }
    for (const fn of [...this.listeners]) fn()
  }
}

function base64ToBytes(b64: string): ArrayBuffer | null {
  try {
    const bin = atob(b64)
    const out = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
    return out.buffer
  } catch {
    return null
  }
}

/**
 * Apply one SSE event to a clip.
 *
 * The same five events arrive inside /chat (voice mode) and as the whole body
 * of POST /tts — the backend emits both from one helper — so both paths come
 * through here and cannot drift apart.
 *
 * @returns true if it was a speech event, handled or not.
 */
export function applySpeechEvent(clip: SpeechClip, type: string, data: unknown): boolean {
  const d = (data ?? {}) as Record<string, unknown>
  switch (type) {
    case 'speech_start':
      clip.begin({
        voiceVersion: typeof d.voice_version === 'string' ? d.voice_version : '',
        // As sent, '' if absent — never a guess. See SpeechMeta.codec.
        codec: typeof d.codec === 'string' ? d.codec : '',
        sampleRate: typeof d.sample_rate === 'number' ? d.sample_rate : 0,
        lang: typeof d.lang === 'string' ? d.lang : undefined,
        estimatedAudioS: typeof d.estimated_audio_s === 'number' ? d.estimated_audio_s : undefined,
      })
      return true
    case 'speech_chunk':
      clip.addChunk(
        typeof d.seq === 'number' ? d.seq : -1,
        typeof d.audio === 'string' ? base64ToBytes(d.audio) : null,
      )
      return true
    case 'speech_end':
      clip.finish(typeof d.chunks === 'number' ? d.chunks : undefined)
      return true
    case 'speech_failed':
      // The text is still there and readable — a missing voice is not worth an
      // error bubble. The console is for diagnosis only.
      console.warn('[TTS]', typeof d.error === 'string' ? d.error : 'speech failed')
      clip.fail(typeof d.error === 'string' ? d.error : 'speech failed')
      return true
    case 'speech_disabled':
      // TTS is not configured on this deployment. That is a setting, not a
      // fault, so it stays out of the console as well as out of the chat.
      clip.fail(CLIP_DISABLED)
      return true
    default:
      return false
  }
}

// ── The player ───────────────────────────────────────────────────────────────

/** Anything with a mouth — AvatarController, in practice. */
export interface LipSyncTarget {
  startLipSync: (analyser: AnalyserNode) => void
  stopLipSync: () => void
}

/**
 * `buffering` is the measured wait before a fresh stream first sounds
 * (computeStartTime — see play()). It exists because the UI has to be able to
 * tell "nothing is happening" from "this clip is the active one and is about
 * to speak": without it the player reported `idle` with a null clip while it
 * held a clip and a pending run, so the speaker button showed neither its
 * spinner nor its pause icon and looked like a dead control — and a click on
 * another message tore the waiting clip down for good. Everything that acts
 * on a live run (pause, seek, progress) still requires `playing`.
 */
export type PlayerStatus = 'idle' | 'buffering' | 'playing' | 'paused'

export interface PlayerSnapshot {
  /** The clip being played or paused; null when idle. */
  clip: SpeechClip | null
  status: PlayerStatus
}

/** One continuous stretch of playback: from a play, resume or seek to the next. */
interface Run {
  scheduler: ChunkScheduler
  sources: Set<AudioBufferSourceNode>
  segments: Placement[]
  /** Clip time the run started from. */
  from: number
}

/**
 * Waiting on the first three decoded chunks before a fresh stream sounds.
 * The samples carry arrival instants on the context clock, so
 * computeStartTime can place the first chunk where the run never starves.
 * Cleared by teardown, a superseding play, or finishMeasuring — whichever
 * comes first, so an aborted wait can never sound.
 */
interface MeasureState {
  samples: (ChunkArrival | undefined)[]
  controller: LipSyncTarget | null
  gen: number
  /**
   * Fires at the prior-based safe start (priorStartTime), so a stream does
   * not have to sit through three chunks before it may sound. Whichever
   * comes first wins: the third sample cancels this timer and uses the
   * measured start; this timer cancels the wait and starts on the prior.
   *
   * A short reply is the case that needs it. Three chunks take ~8.7s to
   * arrive, but 5.6s of audio only needs ~3.2s of buffer — the old code
   * spent five seconds of silence proving something it could have assumed.
   */
  timer: ReturnType<typeof setTimeout> | null
}

/**
 * How long to wait for `resume()` to take. Without a user gesture its promise
 * never settles (the spec parks it until one happens) — and resolving then, on
 * some unrelated click, would start a reply the user had long stopped
 * expecting.
 */
const RESUME_WAIT_MS = 250

async function runningContext(): Promise<AudioContext | null> {
  let ctx: AudioContext
  try {
    ctx = ensureAudioContext()
  } catch {
    return null // no Web Audio at all
  }
  if (ctx.state !== 'running') {
    await Promise.race([
      ctx.resume().catch(() => undefined),
      new Promise((r) => setTimeout(r, RESUME_WAIT_MS)),
    ])
  }
  return ctx.state === 'running' ? ctx : null
}

/**
 * Start the AudioContext from inside a user gesture.
 *
 * Call it synchronously in the click or keypress, before any `await`: Safari
 * and iOS WebViews honour `resume()` only from the gesture's own call stack.
 * Once running, the context stays running, so audio that lands seconds later —
 * outside any gesture — can still play.
 */
export function unlockSpeechAudio(): void {
  try {
    ensureAudioContext()
  } catch {
    // No Web Audio: nothing to unlock, and nothing will play either.
  }
}

class SpeechPlayer {
  private snap: PlayerSnapshot = { clip: null, status: 'idle' }
  private readonly listeners = new Set<() => void>()
  private ctx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  /** Bumped by every play/pause/stop, so a `play` still waiting on resume()
   *  can tell the user has since moved on, and drop itself. */
  private gen = 0
  private clip: SpeechClip | null = null
  private unsubscribeClip: (() => void) | null = null
  /** Decoded buffers for `clip` by seq (null = could not decode). Kept across
   *  pause/resume/seek; dropped when the clip stops or ends. */
  private decoded: (AudioBuffer | null | undefined)[] = []
  private readonly decoding = new Set<number>()
  private decodeFailures = 0
  private run: Run | null = null
  private measure: MeasureState | null = null
  private pausedAt = 0
  /** Whoever `startLipSync` was called on, so exactly that one gets the stop. */
  private mouth: LipSyncTarget | null = null

  subscribe = (fn: () => void): (() => void) => {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  getSnapshot = (): PlayerSnapshot => this.snap

  /**
   * Play `clip` from clip time `from`, stopping whatever else is playing — one
   * voice at a time. A clip still streaming is fine: its chunks are scheduled
   * as they arrive.
   *
   * A FRESH stream (`from` 0, still arriving) does not sound at once: the
   * first three decoded chunks are measured first (computeStartTime), because
   * generation runs slower than playback and starting immediately starves
   * from ~chunk 3. That wait still resolves true — accepted, will sound —
   * since false reads as "failed, press replay". Resume, seek and cache
   * replay start at once, like before.
   *
   * @returns false when nothing started: the browser has not allowed audio yet
   *          (autoplay policy), a newer call superseded this one, or the clip
   *          failed. The clip keeps its audio either way.
   */
  async play(clip: SpeechClip, controller: LipSyncTarget | null, from = 0): Promise<boolean> {
    const gen = ++this.gen
    const ctx = await runningContext()
    if (gen !== this.gen || !ctx) return false
    if (clip.status === 'failed') return false
    if (this.clip !== clip) {
      this.teardown()
      this.adopt(clip, ctx)
    } else {
      this.stopRun()
    }
    if (from === 0 && !clip.settled) {
      this.measure = { samples: [], controller, gen, timer: null }
      // Report the wait, do not stay silent about it: this clip IS the active
      // one from here on, and every subscriber needs to see that.
      this.setSnap('buffering')
      return true
    }
    this.startRun(ctx, from, controller)
    return true
  }

  pause(): void {
    if (this.snap.status !== 'playing') return
    this.gen++
    this.pausedAt = this.position()
    this.stopRun()
    // The mouth stops with the sound, not a moment later over silence.
    this.releaseMouth()
    this.setSnap('paused')
  }

  resume(controller: LipSyncTarget | null): Promise<boolean> {
    const clip = this.clip
    if (this.snap.status !== 'paused' || !clip) return Promise.resolve(false)
    return this.play(clip, controller, this.pausedAt)
  }

  /** Jump to `fraction` of the audio decoded so far. */
  seek(fraction: number): void {
    const ctx = this.ctx
    if (!ctx || !this.clip || this.snap.status === 'idle') return
    const target = Math.min(1, Math.max(0, fraction)) * this.knownDuration()
    if (this.snap.status === 'paused') {
      this.pausedAt = target
      return
    }
    const mouth = this.mouth
    this.stopRun()
    this.startRun(ctx, target, mouth)
  }

  /** Stop and forget the current clip. Safe to call when nothing plays. */
  stop(): void {
    this.gen++
    this.teardown()
  }

  /** Seconds into the current clip. */
  position(): number {
    if (this.snap.status === 'paused') return this.pausedAt
    const run = this.run
    const ctx = this.ctx
    if (!run || !ctx) return 0
    return mediaPositionAt(run.segments, ctx.currentTime, run.from)
  }

  /**
   * Position over the audio decoded so far, 0..1. While a clip is still
   * streaming the total keeps growing (generation runs slower than playback,
   * ~0.64–0.74× measured, so the first chunks are deliberately held back by
   * the measured buffer), and the bar runs to the end once the last chunk
   * lands. It never moves backwards.
   */
  progress(): number {
    const total = this.knownDuration()
    return total > 0 ? Math.min(1, this.position() / total) : 0
  }

  private knownDuration(): number {
    let total = 0
    for (const b of this.decoded) if (b) total += b.duration
    return total
  }

  private adopt(clip: SpeechClip, ctx: AudioContext): void {
    this.clip = clip
    this.ctx = ctx
    this.decoded = []
    this.decoding.clear()
    this.decodeFailures = 0
    this.unsubscribeClip = clip.subscribe(() => this.onClipChange())
    this.decodeArrived()
  }

  private teardown(): void {
    this.stopRun()
    this.releaseMouth()
    this.unsubscribeClip?.()
    this.unsubscribeClip = null
    this.clip = null
    if (this.measure?.timer) clearTimeout(this.measure.timer)
    this.measure = null
    this.decoded = []
    this.decoding.clear()
    this.pausedAt = 0
    this.setSnap('idle')
  }

  private onClipChange(): void {
    const clip = this.clip
    if (!clip) return
    // speech_failed, or the stream was aborted: stop, rather than play out a
    // fragment of an answer that is not coming.
    if (clip.status === 'failed') {
      this.stop()
      return
    }
    this.decodeArrived()
    // Stream ended before three chunks: everything is in hand, so measure
    // with what arrived (computeStartTime starts at once on <3 samples).
    if (this.measure && clip.status === 'complete') this.finishMeasuring()
    this.maybeFinish()
  }

  /** Start decoding every chunk that has arrived and is not decoded yet —
   *  all at once, in any order; the scheduler puts them back in order. */
  private decodeArrived(): void {
    const clip = this.clip
    const ctx = this.ctx
    if (!clip || !ctx) return
    for (let seq = 0; seq < clip.chunks.length; seq++) {
      if (this.clip !== clip) return // a settle below finished and tore down
      if (this.decoded[seq] !== undefined || this.decoding.has(seq)) continue
      const bytes = clip.chunks[seq]
      if (bytes === undefined) continue // still on its way
      if (bytes === null) {
        this.settle(seq, null) // arrived unusable: skip it, do not wait for it
        continue
      }
      this.decoding.add(seq)
      // decodeAudioData DETACHES the buffer it is given. Hand it a copy: the
      // original is what a replay, and the IndexedDB write, still need.
      ctx.decodeAudioData(bytes.slice(0)).then(
        (buf) => {
          if (this.clip === clip) this.settle(seq, buf)
        },
        (err: unknown) => {
          if (this.clip !== clip) return
          this.noteDecodeFailure(clip, err)
          this.settle(seq, null)
        },
      )
    }
  }

  private noteDecodeFailure(clip: SpeechClip, err: unknown): void {
    // Once per clip, not per chunk. A device that cannot decode the codec
    // fails every chunk, and 25 identical warnings bury the line that matters.
    if (this.decodeFailures++ === 0) {
      console.warn(
        `[TTS] this device could not decode a ${clip.meta?.codec ?? 'speech'} chunk; skipping it`,
        err,
      )
    }
  }

  private settle(seq: number, buf: AudioBuffer | null): void {
    if (!this.clip) return
    this.decoding.delete(seq)
    this.decoded[seq] = buf
    const run = this.run
    const ctx = this.ctx
    if (run && ctx) {
      this.place(run, run.scheduler.offer(seq, buf ? buf.duration : null, ctx.currentTime))
    } else if (ctx) {
      this.noteArrival(seq, buf, ctx.currentTime)
    }
    this.maybeFinish()
  }

  /**
   * Record one decoded chunk toward the three-sample measurement, and start
   * the run the moment the third lands. Runs only while no run exists — once
   * started, arrivals go straight to the scheduler above.
   */
  private noteArrival(seq: number, buf: AudioBuffer | null, now: number): void {
    const m = this.measure
    if (!m || !this.clip || m.gen !== this.gen) return
    if (seq >= 3) return
    if (buf === null) {
      // A failed chunk inside the first three cannot be measured around:
      // fall back to starting now, and the scheduler skips it like before.
      this.finishMeasuring()
      return
    }
    m.samples[seq] = { at: now, duration: buf.duration }
    if (m.samples[0] && m.samples[1] && m.samples[2]) {
      this.finishMeasuring()
      return
    }
    // First chunk: arm the prior-based start. Everything needed is known now
    // — when generation began producing (this arrival) and how much audio is
    // coming (the server's estimate) — so waiting for two more chunks only
    // buys precision, and costs silence a short reply cannot afford.
    const est = this.clip?.meta?.estimatedAudioS
    const ctx = this.ctx
    if (seq === 0 && m.timer === null && ctx && typeof est === 'number' && est > 0) {
      const at = Math.min(priorStartTime(now, est), now + MAX_START_DELAY_S)
      m.timer = setTimeout(
        () => {
          if (this.measure === m) this.finishMeasuring()
        },
        Math.max(0, (at - ctx.currentTime) * 1000),
      )
    }
  }

  private finishMeasuring(): void {
    const m = this.measure
    const clip = this.clip
    const ctx = this.ctx
    this.measure = null
    if (m?.timer !== null && m?.timer !== undefined) clearTimeout(m.timer)
    if (!m || !clip || !ctx || m.gen !== this.gen) return
    const startAt = computeStartTime(
      m.samples.filter((s): s is ChunkArrival => !!s),
      clip.meta?.estimatedAudioS,
      ctx.currentTime,
    )
    this.startRun(ctx, 0, m.controller, startAt)
  }

  private startRun(ctx: AudioContext, from: number, controller: LipSyncTarget | null, firstStartAt?: number): void {
    // Array.from, not .map: `decoded` is sparse while decodes are in flight,
    // and .map would keep the holes as holes instead of reading them as
    // "not decoded yet".
    const durations = Array.from({ length: this.decoded.length }, (_, i) => {
      const b = this.decoded[i]
      return b === undefined ? undefined : b === null ? null : b.duration
    })
    const start = locate(durations, from)
    const run: Run = {
      scheduler: new ChunkScheduler(start, undefined, firstStartAt),
      sources: new Set(),
      segments: [],
      from: start.mediaStart + start.offset,
    }
    this.run = run

    this.analyser ??= createSpeechAnalyser()
    if (this.mouth && this.mouth !== controller) this.releaseMouth()
    if (controller) {
      // Started once for the whole run, not per chunk: between chunks the
      // analyser just reads silence for a few ms and the mouth eases, instead
      // of snapping shut and open at every join.
      controller.startLipSync(this.analyser)
      this.mouth = controller
    }
    this.setSnap('playing')

    for (let seq = start.seq; seq < this.decoded.length; seq++) {
      const b = this.decoded[seq]
      if (b !== undefined) this.place(run, run.scheduler.offer(seq, b ? b.duration : null, ctx.currentTime))
    }
    this.maybeFinish()
  }

  private place(run: Run, placements: Placement[]): void {
    const ctx = this.ctx
    const analyser = this.analyser
    if (!ctx || !analyser) return
    for (const p of placements) {
      const buf = this.decoded[p.seq]
      if (!buf) continue
      const src = ctx.createBufferSource()
      src.buffer = buf
      src.connect(analyser)
      src.onended = () => {
        run.sources.delete(src)
        src.disconnect()
        if (this.run === run) this.maybeFinish()
      }
      src.start(p.startAt, p.offset)
      run.sources.add(src)
      run.segments.push(p)
      if (p.lateBy > 0) {
        // The one number that says streaming is starving. Generation runs
        // SLOWER than playback here (~0.64–0.74× measured), so this fires
        // when the measured buffer was short for this turn's actual rate —
        // not when something between SpeechLLm and here holds chunks back.
        console.warn(
          `[TTS] chunk ${p.seq} started ${Math.round(p.lateBy * 1000)} ms after its slot — ` +
            'an audible gap: chunks are arriving slower than they play',
        )
      }
    }
  }

  private maybeFinish(): void {
    const run = this.run
    const clip = this.clip
    if (!run || !clip) return
    // Still streaming: a drained queue is starvation, not the end.
    if (clip.status !== 'complete') return
    if (run.scheduler.waitingFor < clip.chunks.length) return // decodes in flight
    if (run.sources.size > 0) return // still sounding

    const undecodable = this.decodeFailures > 0 && !this.decoded.some((b) => b instanceof AudioBuffer)
    // Tear down BEFORE marking: marking notifies the clip's subscribers, and
    // this player is one of them until teardown unsubscribes it.
    this.teardown()
    if (undecodable) clip.markUndecodable()
  }

  private stopRun(): void {
    const run = this.run
    this.run = null
    if (!run) return
    for (const src of run.sources) {
      src.onended = null
      try {
        src.stop()
      } catch {
        // Scheduled but never started, or already ended.
      }
      src.disconnect()
    }
    run.sources.clear()
  }

  private releaseMouth(): void {
    this.mouth?.stopLipSync()
    this.mouth = null
  }

  private setSnap(status: PlayerStatus): void {
    const clip = status === 'idle' ? null : this.clip
    if (this.snap.status === status && this.snap.clip === clip) return
    this.snap = { clip, status }
    for (const fn of [...this.listeners]) fn()
  }
}

export const speechPlayer = new SpeechPlayer()
