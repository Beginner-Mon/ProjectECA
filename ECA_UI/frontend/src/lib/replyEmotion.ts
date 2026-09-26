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

export function parseReplyEmotion(data: unknown): { name: CanonicalEmotion; intensity: number } | null {
  if (!data || typeof data !== 'object') return null
  const { name, intensity } = data as { name?: unknown; intensity?: unknown }
  if (typeof name !== 'string' || !(CANONICAL_EMOTIONS as readonly string[]).includes(name)) return null
  const raw = typeof intensity === 'number' && Number.isFinite(intensity) ? intensity : DEFAULT_INTENSITY
  return { name: name as CanonicalEmotion, intensity: Math.min(1, Math.max(0, raw)) }
}
