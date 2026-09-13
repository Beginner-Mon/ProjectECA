/**
 * Character catalog client.
 *
 * Two different origins, and the split is not arbitrary:
 *
 *   the CATALOG (this file's fetches)  → API Gateway. It is an API call.
 *   the MODEL FILES (`vrm_url` below)  → CloudFront. They are 9-17 MB binaries,
 *                                        over API Gateway's 10 MB limit.
 *
 * Until 20-08 both went through CloudFront, on a /characters* behavior pointing
 * at a Lambda Function URL. The catalog moved to the gateway with the rest of the
 * API; the .vrm files stayed where a CDN is the right answer.
 *
 * `vrm_url` is an absolute URL stored in the database by
 * scripts/upload_characters_to_s3.py, so it already points at CloudFront — this
 * file does not build it.
 */

import { API_GATEWAY, ASSET_BASE } from './apiBase'

export { ASSET_BASE }

/** Auto-extracted from the .vrm GLB by scripts/upload_characters_to_s3.py. */
export interface VrmMetadata {
  joint_count: number
  spine_count: number
  has_humanoid_rig: boolean
  blendshape_groups: {
    total: number
    emotions: number
    visemes: number
    blinks: number
    look_ats: number
    customs: number
  }
  has_blink: boolean
  has_look_at: boolean
  /** Empty when the model is fully usable; each entry is a reason it is not. */
  incompatible_reasons: string[]
  vrm_version: string
  file_size_bytes: number
  extracted_at: string
}

export interface CharacterLite {
  slug: string
  display_name: string
  description: string | null
  thumbnail_url: string | null
  vrm_metadata: VrmMetadata | null
}

export interface Character extends CharacterLite {
  vrm_url: string
  vrm_metadata: VrmMetadata
  voice_language: string
  sort_order: number
  /** Chat-surface copy — greeting, stage labels, error line, input placeholder. */
  ui_strings?: Record<string, unknown>
  /** Pre-rendered clips behind CloudFront signed URLs (characters/{slug}/audio/{hash}.ogg). */
  static_audio?: Record<string, Record<string, string>>
}



/** True when nothing blocks this model from being used. */
export function isCompatible(character: Character | CharacterLite): boolean {
  return (character.vrm_metadata?.incompatible_reasons?.length ?? 0) === 0
}

/** Human-readable reason a model is greyed out, or null when it is fine. */
export function incompatibilityReason(character: Character | CharacterLite): string | null {
  const reasons = character.vrm_metadata?.incompatible_reasons ?? []
  if (reasons.length === 0) return null
  const readable: Record<string, string> = {
    'spine_count < 3': 'Rig has too few spine joints for motion retargeting',
    'blendshape_groups.total == 0': 'Model has no expression blendshapes',
    'no humanoid rig': 'Model has no humanoid rig',
  }
  return reasons.map((r) => readable[r] ?? r).join('; ')
}

export async function fetchCharacters(signal?: AbortSignal): Promise<CharacterLite[]> {
  const res = await fetch(`${API_GATEWAY}/characters`, { signal })
  if (!res.ok) throw new Error(`GET /characters failed: ${res.status}`)
  const data = (await res.json()) as { characters: CharacterLite[]; total: number }
  return data.characters ?? []
}

export async function fetchCharacter(slug: string, signal?: AbortSignal): Promise<Character> {
  const res = await fetch(`${API_GATEWAY}/characters/${encodeURIComponent(slug)}`, { signal })
  if (!res.ok) throw new Error(`GET /characters/${slug} failed: ${res.status}`)
  return (await res.json()) as Character
}

export async function fetchAvatarProfile(
  slug: string,
  signal?: AbortSignal
): Promise<unknown> {
  const res = await fetch(`${API_GATEWAY}/characters/${slug}/avatar-profile`, { signal })
  if (!res.ok) throw new Error(`GET /characters/${slug}/avatar-profile failed: ${res.status}`)
  return res.json()
}

// ── Static audio (D7) ─────────────────────────────────────────────────────

/**
 * Get a signed URL for a pre-rendered clip (e.g. greeting) without calling TTS.
 *
 * The URL comes from characters.static_audio via /characters (signed at read
 * time, characters/*/audio/* via CloudFront trusted key group). No request
 * reaches SpeechLLm. On 403 (signature expired, 5m TTL) the caller should
 * refetch /characters once and retry — this helper does that for the fetch
 * check, but the plain `getStaticAudioUrl` below just returns the first URL
 * so the caller can decide when to retry.
 */
export function getStaticAudioUrl(
  character: Character,
  kind: string,
  lang: string,
): string | null {
  const sa = character.static_audio
  if (!sa || typeof sa !== 'object') return null
  const byKind = (sa as Record<string, Record<string, string>>)[kind]
  if (!byKind || typeof byKind !== 'object') return null
  // Exact lang, then fallback to the character's own voice_language, then vi
  return byKind[lang] ?? byKind[character.voice_language] ?? byKind['vi'] ?? byKind['en'] ?? null
}

/**
 * Fetch a static clip URL and verify it is still fetchable, retrying once on 403.
 *
 * The signed URL lives ~5 minutes (SIGNED_URL_TTL). A tab left open past that
 * will 403 on the next play. This helper does a HEAD, and on 403 refetches
 * /characters once to get a fresh signature, then returns the new URL. The
 * caller then plays the returned URL (no second HEAD needed — the Audio
 * element's own fetch is the real use).
 *
 * Returns null if no clip exists for this character/kind/lang.
 */
export async function fetchStaticAudioUrlWithRetry(
  slug: string,
  kind: string,
  lang: string,
  signal?: AbortSignal,
): Promise<string | null> {
  let character: Character
  try {
    character = await fetchCharacter(slug, signal)
  } catch {
    return null
  }
  let url = getStaticAudioUrl(character, kind, lang)
  if (!url) return null

  // Probe the URL with HEAD — cheap, and lets us detect 403 before handing
  // the URL to an <audio> element that would surface it as a generic media
  // error with no status. If HEAD is blocked by CORS, we fall back to
  // returning the URL anyway and let the audio element try; the retry will
  // happen on its error event if the caller wires it.
  try {
    const res = await fetch(url, { method: 'HEAD', signal })
    if (res.status !== 403) return url
    // 403 — signature expired, refetch once
    character = await fetchCharacter(slug, signal)
    const retryUrl = getStaticAudioUrl(character, kind, lang)
    return retryUrl ?? url
  } catch {
    // Network or CORS failure on HEAD — return the URL and let the player try
    return url
  }
}

/**
 * Play a static clip URL directly (no TTS), handling 403 retry once.
 * Returns the Audio element so the caller can stop it, or null if there
 * was no clip. Fire-and-forget is fine for greeting — it is chrome, not
 * transcript, and a failed play is not an error bubble.
 */
export async function playStaticAudio(
  slug: string,
  kind: string,
  lang: string,
  signal?: AbortSignal,
): Promise<HTMLAudioElement | null> {
  const url = await fetchStaticAudioUrlWithRetry(slug, kind, lang, signal)
  if (!url) return null
  const audio = new Audio(url)
  audio.crossOrigin = 'anonymous'
  // If the HEAD probe above was skipped (CORS), the Audio element may still
  // 403. On error, refetch once and retry the Audio src.
  let retried = false
  audio.addEventListener('error', async () => {
    if (retried) return
    retried = true
    try {
      const retryUrl = await fetchStaticAudioUrlWithRetry(slug, kind, lang, signal)
      if (retryUrl && retryUrl !== url) {
        audio.src = retryUrl
        await audio.play().catch(() => {})
      }
    } catch {
      // give up — greeting stays text-only
    }
  })
  try {
    await audio.play()
  } catch {
    // Autoplay blocked — wait for user gesture (speaker button would still work)
    // For greeting, we silently ignore; the text is already there.
  }
  return audio
}
