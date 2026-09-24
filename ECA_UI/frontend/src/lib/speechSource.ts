/**
 * Where a message's voice comes from, and where it goes afterwards.
 *
 * Two ways speech reaches the browser, one function each:
 *   - routeSpeechEvent: voice mode — the events arrive inside the /chat stream
 *   - openSpeech:       the speaker button on a message with no audio in memory
 *
 * Both push every speech_* event through `routeSpeechEvent`, so the cache sees
 * the same lifecycle whichever path produced the audio: `speech_start` retires
 * rows made with an older recording of the voice, `speech_end` stores the clip.
 *
 * Kept apart from speechPlayer.ts and ttsCache.ts on purpose. This is the only
 * one of the three that imports api.ts (and through it aws-amplify), which is
 * what lets the other two be tested under vitest's plain `node` environment.
 */

import { cognitoSub, speakText } from './api'
import { CLIP_ABORTED, SpeechClip, applySpeechEvent } from './speechPlayer'
import { cacheKeyFor, dropStaleVoice, readCachedClip, writeCachedClip } from './ttsCache'

/** What was spoken, and by whom — the cache key's two inputs. */
export interface SpeechOrigin {
  text: string
  persona: string
}

/**
 * Apply one SSE event to `clip`, and keep the cache in step with it.
 *
 * @returns true if it was a speech event.
 */
export function routeSpeechEvent(
  clip: SpeechClip,
  type: string,
  data: unknown,
  origin: SpeechOrigin,
): boolean {
  if (!applySpeechEvent(clip, type, data)) return false
  if (type === 'speech_start') void forgetOlderVoice(clip, origin.persona)
  else if (type === 'speech_end') void remember(clip, origin)
  return true
}

/**
 * The server just said which recording it cloned. Rows for this character made
 * from a different one are stale — the voice was re-recorded — and would
 * otherwise keep playing the old voice until their TTL ran out.
 */
async function forgetOlderVoice(clip: SpeechClip, persona: string): Promise<void> {
  const meta = clip.meta
  if (!meta?.voiceVersion) return
  const sub = await cognitoSub()
  if (sub) await dropStaleVoice(sub, persona, meta.lang, meta.voiceVersion)
}

async function remember(clip: SpeechClip, { text, persona }: SpeechOrigin): Promise<void> {
  const meta = clip.meta
  if (!meta || !clip.intact) return
  // Resolved now, not when the stream began: a user who signed out while the
  // answer was being voiced has no sub any more, and must not get a write.
  const sub = await cognitoSub()
  if (!sub) return // demo mode: no identity, no cache
  const key = await cacheKeyFor(text, persona)
  if (!key) return
  await writeCachedClip(sub, key, {
    persona,
    voiceVersion: meta.voiceVersion,
    lang: meta.lang,
    codec: meta.codec,
    sampleRate: meta.sampleRate,
    chunks: clip.chunks.filter((c): c is ArrayBuffer => c instanceof ArrayBuffer),
  })
}

/**
 * Synthesis that is on its way, by message.
 *
 * It lives here rather than in the component that started it, because the
 * component does not outlive the request: closing the conversation, switching
 * to another one, or anything else that unmounts the speaker button used to
 * abort a synthesis already in flight — the button came back as a plain
 * speaker, the ~25 seconds of work were thrown away, and the next click paid
 * for all of it again.
 *
 * The audio itself never had that problem: speechPlayer is a singleton, so a
 * clip that had started playing kept playing through the unmount. This makes
 * the loading half behave the same way.
 *
 * Entries stay until they fail or are cancelled. A completed one is worth
 * keeping for the session: it is the same clip IndexedDB now holds, minus the
 * read.
 */
interface LiveSpeech {
  clip: SpeechClip
  controller: AbortController
}

const live = new Map<string, LiveSpeech>()

const liveKey = (text: string, persona: string): string => `${persona}\u0000${text}`

/** The clip already being synthesised (or just finished) for this message. */
export function liveSpeech(text: string, persona: string): SpeechClip | null {
  const entry = live.get(liveKey(text, persona))
  if (!entry) return null
  if (entry.clip.status === 'failed') {
    live.delete(liveKey(text, persona))
    return null
  }
  return entry.clip
}

/**
 * Give up on a synthesis: stop the request, drop the clip.
 *
 * Only ever called from a deliberate stop. Navigating away is NOT that — see
 * the note above.
 */
export function cancelSpeech(text: string, persona: string): void {
  const key = liveKey(text, persona)
  const entry = live.get(key)
  if (!entry) return
  live.delete(key)
  if (!entry.clip.settled) entry.controller.abort()
}

/**
 * A clip for `text` in `persona`'s voice: this browser's cache first, the
 * network second.
 *
 * A cache hit comes back complete and costs no request at all. A miss comes
 * back at once as an empty clip that POST /tts then streams into, so the caller
 * can start playing it straight away and hear the first chunk ~0.5s later; it
 * is cached when `speech_end` arrives.
 *
 * Never rejects. A failed synthesis (503 where TTS is not configured, a network
 * error) shows up as the clip failing, which is what the speaker button
 * watches anyway.
 */
export async function openSpeech(
  text: string,
  persona: string,
): Promise<SpeechClip> {
  const existing = liveSpeech(text, persona)
  if (existing) return existing
  const [sub, key] = await Promise.all([cognitoSub(), cacheKeyFor(text, persona)])
  if (sub && key) {
    const hit = await readCachedClip(sub, key)
    if (hit) {
      return SpeechClip.fromCache(
        {
          voiceVersion: hit.meta.voiceVersion,
          codec: hit.meta.codec,
          sampleRate: hit.meta.sampleRate,
          lang: hit.meta.lang,
        },
        hit.chunks,
      )
    }
  }

  const clip = new SpeechClip()
  const origin = { text, persona }
  const controller = new AbortController()
  live.set(liveKey(text, persona), { clip, controller })
  speakText(text, persona, (type, data) => routeSpeechEvent(clip, type, data, origin), controller.signal).then(
    () => {
      // The body ended without speech_end or speech_failed. Whatever arrived is
      // a fragment; failing the clip stops playback and lets a click retry.
      if (!clip.settled) {
        console.warn('[TTS] on-demand stream closed before speech_end')
        clip.fail('stream closed before speech_end')
      }
    },
    (e: unknown) => {
      if ((e as Error)?.name === 'AbortError') {
        clip.fail(CLIP_ABORTED)
        return
      }
      console.warn('[TTS] on-demand synthesis failed:', e)
      clip.fail(e instanceof Error ? e.message : String(e))
    },
  )
  return clip
}
