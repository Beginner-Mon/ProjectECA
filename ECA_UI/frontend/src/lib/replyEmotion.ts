/**
 * Reply-driven emotion — the browser side.
 *
 * The backend reads an [emotion: …] tag off the start of each reply and sends
 * it as its own `emotion` SSE event, ahead of the text and already filtered by
 * the health rules (agenticRAG/langgraph_agents/shared/reply_emotion.py). This
 * validates it before it reaches the avatar: anything unexpected is dropped, so
 * a bad event can never break the face or the chat.
 */

import { CANONICAL_EMOTIONS, type CanonicalEmotion } from '../avatar/AvatarProfile'

/** How long the face takes to ease into the reply's emotion. */
export const REPLY_EMOTION_FADE_MS = 700

const DEFAULT_INTENSITY = 0.6

/**
 * The planner's read of the user's message (`user_affect` on its `stage`
 * event) → the face the avatar holds while it thinks. Kept at or under 0.5 so
 * the model's own expression overrides cannot mute blink or gaze.
 */
const LISTENING_FACES: Record<string, { name: CanonicalEmotion; intensity: number } | null> = {
  pain: { name: 'sad', intensity: 0.3 },
  worry: { name: 'sad', intensity: 0.3 },
  sad_news: { name: 'sad', intensity: 0.3 },
  thanks: { name: 'happy', intensity: 0.4 },
  greeting: { name: 'happy', intensity: 0.4 },
  good_news: { name: 'happy', intensity: 0.4 },
  neutral: null,
}

/** Planner tags whose reply is safety content: the face stays neutral. */
const SAFETY_TAGS = ['red_flag_screen', 'referral_advice']

/** Listening face for a planner `stage` event. Unknown affect → null (no change). */
export function listeningFace(affect: unknown): { name: CanonicalEmotion; intensity: number } | null {
  if (typeof affect !== 'string') return null
  return LISTENING_FACES[affect] ?? null
}

/** True when the planner tagged this turn as safety content. */
export function isSafetyTurn(requiredOutputs: unknown): boolean {
  return Array.isArray(requiredOutputs) && requiredOutputs.some((t) => SAFETY_TAGS.includes(t as string))
}

/** A brief "sorry" face when something the user waits on fails (motion, turn error). */
export const SETBACK_FACE = { name: 'sad' as const, intensity: 0.25, fadeMs: 500 }

export function parseReplyEmotion(data: unknown): { name: CanonicalEmotion; intensity: number } | null {
  if (!data || typeof data !== 'object') return null
  const { name, intensity } = data as { name?: unknown; intensity?: unknown }
  if (typeof name !== 'string' || !(CANONICAL_EMOTIONS as readonly string[]).includes(name)) return null
  const raw = typeof intensity === 'number' && Number.isFinite(intensity) ? intensity : DEFAULT_INTENSITY
  return { name: name as CanonicalEmotion, intensity: Math.min(1, Math.max(0, raw)) }
}
