/**
 * The greeting's voice: a pre-rendered /audio clip, cached, lip-synced.
 *
 * Replaces the D7 path (signed URLs inside the public /characters response —
 * anyone could fetch them — played through a bare <audio> element the avatar
 * could not lip-sync to). Now:
 *
 *   - `slot`/`text` arrive as parameters, captured by the caller at the same
 *     moment it wrote the on-screen bubble (`lib/greeting.ts`'s
 *     `resolveGreeting`) — this module never calls `getTimeSlot()` or
 *     `getGreeting()` itself, so caption and voice cannot disagree even if a
 *     render lands between the two (see the frozen-slot fix in ChatContext);
 *   - bytes come from GET /characters/{slug}/audio (Cognito, contract B) or
 *     from this browser's cache — never from SpeechLLm, so a greeting costs
 *     no synthesis;
 *   - playback goes through speechPlayer, the same Web Audio + analyser path
 *     TTS uses, so the avatar lip-syncs;
 *   - a text_sha256 mismatch (clip rendered from other words) plays NOTHING
 *     and caches nothing — a wrong voice is worse than silence.
 *
 * A greeting is chrome shaped like a message: every failure here resolves to
 * text-only, never an error bubble. `lang` is the site locale the reader
 * chose, not a guess at any message's language.
 */

import type { Locale } from '@/i18n/locale'

import { cognitoSub } from './api'
import { fetchClips, type Character } from './characters'
import type { TimeSlot } from './characterCopy'
import { speechPlayer, SpeechClip, type LipSyncTarget } from './speechPlayer'
import {
  readCachedClip,
  sha256Hex,
  STATIC_CACHE_TTL_MS,
  writeCachedClip,
} from './ttsCache'

export interface GreetingRequest {
  character: Character
  locale: Locale
  /**
   * The slot and the exact words already on screen, captured once by the
   * caller at the moment it wrote the greeting bubble (ChatContext /
   * `lib/greeting.ts`'s `resolveGreeting`). `playGreeting` must never
   * recompute either from the live clock: doing so is what let the played
   * clip (a fresh `getTimeSlot()` read) diverge from the caption still
   * showing the previous slot's text after an hour boundary passed mid-render.
   */
  slot: TimeSlot
  text: string
  /** Avatar mouth. Null in tests / where no avatar is mounted — plays blind. */
  controller: LipSyncTarget | null
  signal?: AbortSignal
}

/** Cache key carrying every input the bytes depend on (T7.4). */
export function greetingCacheKey(
  slug: string,
  clip: string,
  locale: string,
  audioVersion: string,
  textHash: string,
): string {
  return `static:${slug}:${clip}:${locale}:${audioVersion}:${textHash}`
}

/**
 * Which greeting opening this is (fix #1). A plain string over stable
 * PRIMITIVES — slug, audio_version, locale, slot — so a catalog reload that
 * rebuilds the vrmOptions array with fresh object identities still yields
 * the SAME key, and the ChatContext guard (already greeted for this key?)
 * stays shut. Any real change (avatar, language, hour slot, re-render)
 * yields a different key and may greet again.
 *
 * Lives in characterCopy.ts (re-exported here for callers that already
 * import this module): ChatContext needs it at render time, and a static
 * import of this module would drag speechPlayer into the main bundle,
 * undoing the dynamic-import code split.
 */
export { buildGreetingKey } from './characterCopy'

/** Stop OUR greeting if it is the thing playing — never anyone else's voice. */
function stopOurs(clip: SpeechClip): void {
  if (speechPlayer.getSnapshot().clip === clip) speechPlayer.stop()
}

async function playBytes(
  bytes: ArrayBuffer,
  audioVersion: string,
  locale: string,
  controller: LipSyncTarget | null,
  signal?: AbortSignal,
): Promise<void> {
  const clip = new SpeechClip()
  clip.begin({
    voiceVersion: audioVersion,
    codec: 'audio/ogg',
    sampleRate: 48000,
    lang: locale,
  })
  clip.addChunk(0, bytes.byteLength > 0 ? bytes : null)
  clip.finish(1)

  // The listener OUTLIVES this await on purpose: aborting after the sound
  // started must still stop it (b97eeda2), and the signal object is
  // per-effect-run so nothing leaks — the listener dies with it, at the
  // latest when it fires (removed below) or when the controller is GC'd.
  // stopOurs only ever touches OUR clip, so a late abort can never kill a
  // newer voice that has since taken the player.
  const onAbort = () => {
    signal?.removeEventListener('abort', onAbort)
    stopOurs(clip)
  }
  signal?.addEventListener('abort', onAbort)
  await speechPlayer.play(clip, controller)
  // The signal may have fired while play() was still awaiting the audio
  // context — before adopt, stopOurs could not see our clip yet. Recheck:
  // a sound that starts after its cancellation is a bug, not audio.
  if (signal?.aborted) stopOurs(clip)
}

export async function playGreeting(req: GreetingRequest): Promise<void> {
  const { character, locale, slot, text, controller, signal } = req
  if (signal?.aborted) return

  const slug = character.slug
  const clip = `greeting.${slot}`

  const textHash = await sha256Hex(text)
  if (!textHash || signal?.aborted) return

  const audioVersion = character.audio_version ?? null
  // No version means no clips for this character — text-only, no error.
  if (!audioVersion) return

  const key = greetingCacheKey(slug, clip, locale, audioVersion, textHash)
  // The cache namespace is the Cognito sub (ttsCache.ts) — demo mode plays
  // without caching.
  const sub = await cognitoSub()
  if (signal?.aborted) return

  // Cache hit: play at once, zero network requests.
  if (sub) {
    const hit = await readCachedClip(sub, key)
    if (signal?.aborted) return
    if (hit && hit.chunks.length > 0 && hit.chunks[0] instanceof ArrayBuffer) {
      await playBytes(hit.chunks[0], audioVersion, locale, controller, signal)
      return
    }
  }

  // Miss: one /audio call, then the bytes. Any failure below is text-only.
  let entry
  try {
    const clips = await fetchClips(slug, [clip], locale, signal)
    entry = clips[clip] ?? null
  } catch {
    return
  }
  if (!entry || signal?.aborted) return

  // The words must match the caption on screen, exactly. A clip rendered
  // from other words (persona edited after the render, slot text changed)
  // is refused: not played, not cached — with the clip name in the log so
  // the mismatch is findable.
  if (entry.text_sha256 !== textHash) {
    console.warn(
      `[greeting] text mismatch for ${clip} (${locale}): ` +
        `clip was rendered from other words — not playing, not caching`,
    )
    return
  }

  let bytes: ArrayBuffer
  try {
    const resp = await fetch(entry.url, { signal })
    if (!resp.ok) return
    bytes = await resp.arrayBuffer()
  } catch {
    return
  }
  if (!bytes.byteLength || signal?.aborted) return

  // Cache BEFORE playing: the write clones the buffer, so the clip keeps
  // its own bytes however the store treats them. Demo mode (no sub) plays
  // without caching — the per-browser UUID is not an identity.
  if (sub) {
    await writeCachedClip(sub, key, {
      persona: slug,
      voiceVersion: audioVersion,
      lang: locale,
      codec: 'audio/ogg',
      sampleRate: 48000,
      chunks: [bytes],
      kind: 'static',
      ttlMs: STATIC_CACHE_TTL_MS,
    })
  }
  if (signal?.aborted) return

  await playBytes(bytes, audioVersion, locale, controller, signal)
}
