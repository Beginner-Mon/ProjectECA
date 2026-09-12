/**
 * The tail of one chat turn: when the composer is handed back, when the session
 * list goes stale, and where the reply's speech goes. Everything after the last
 * token, in other words.
 *
 * Pulled out of ChatContext's SSE callback so the ordering can be tested — the
 * callback itself lives inside a React provider, and this project tests in
 * vitest's `node` environment with no DOM and no React renderer. ChatContext
 * supplies the side effects as hooks; the decisions are all here.
 *
 * ── When the composer is released ───────────────────────────────────────────
 * At the FIRST of, in stream order:
 *
 *   session_persisted  the turn is in the database. The earliest moment a next
 *                      message is safe: that turn's memory read will include
 *                      this one. The backend emits it after the graph, BEFORE
 *                      text-to-speech and before its summarizer check — which
 *                      together held the stop button up for 6.5-8s after the
 *                      answer was fully on screen.
 *   speech_start       fallback: persistence failed, so there is no
 *                      session_persisted, but the text is complete.
 *   done               last fallback, for a turn with neither.
 *
 * Releasing is UI only. It never aborts the stream and never touches speech:
 * chunks for this reply keep arriving and playing while the user types the next
 * message. That is why `isCurrent` gates the release and nothing else — a
 * superseded stream must not hand back a composer that now belongs to a newer
 * turn, but its voice is still its own.
 */

import type { SpeechClip } from './speechPlayer'

export interface TurnHooks {
  /** This stream is still the newest one. */
  isCurrent: () => boolean
  /** Stop button back to send, stage label cleared, thinking pose ended.
   *  Called more than once per turn; must be idempotent. */
  releaseComposing: () => void
  /** The session list (titles, ordering) may have changed on the server. */
  markSessionsDirty: () => void
  /** A speech_* event for this reply's clip (and the cache behind it). */
  routeSpeech: (clip: SpeechClip, type: string, data: unknown) => void
  /** Start speaking this reply — voice mode's autoplay. */
  playSpeech: (clip: SpeechClip) => void
}

/**
 * One handler per turn.
 *
 * @param speech the reply's clip in voice mode; undefined when voice is off.
 * @returns a function that takes each SSE event and reports whether it was one
 *          of this module's (session_persisted, speech_*, done).
 */
export function createTurnLifecycle(
  speech: SpeechClip | undefined,
  hooks: TurnHooks,
): (type: string, data: unknown) => boolean {
  let persisted = false
  const release = () => {
    if (hooks.isCurrent()) hooks.releaseComposing()
  }

  return (type, data) => {
    if (type === 'session_persisted') {
      persisted = true
      // Not gated on isCurrent: the row exists whichever stream is newest, and
      // once the composer is released the user may already have sent another
      // message, after which this stream's `done` is no longer current.
      hooks.markSessionsDirty()
      release()
      return true
    }

    if (type.startsWith('speech_')) {
      // No clip means voice was off for this turn and nothing should be
      // arriving at all.
      if (!speech) return true
      hooks.routeSpeech(speech, type, data)
      if (type === 'speech_start') {
        // Autoplay, decided once, at the event. Not gated on isCurrent: the
        // voice belongs to this reply however many messages came after it.
        hooks.playSpeech(speech)
        release()
      }
      return true
    }

    if (type === 'done') {
      if (!hooks.isCurrent()) return true
      hooks.releaseComposing()
      // Only when session_persisted never came (the write failed or the
      // backend predates the event). The sessions panel refetches whenever this
      // flag turns on, so marking it at both would fetch twice per turn.
      if (!persisted) hooks.markSessionsDirty()
      return true
    }

    return false
  }
}
