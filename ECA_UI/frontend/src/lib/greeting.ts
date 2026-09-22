/**
 * When the assistant's opening line may be rewritten, and when it may not.
 *
 * The greeting is the one message nobody sent. It is generated on the client
 * from the character's `ui_strings`, never posted to the backend, and rebuilt
 * from scratch when a past session is restored — so it is not transcript, it is
 * chrome that happens to be shaped like a message.
 *
 * That stops being true the moment a real message sits below it. From then on
 * the screen is a record of a conversation, and editing the top of it would
 * claim something was said that never was. This is the same conclusion
 * ChatContext already reached for character switching:
 *
 *     // Subsequent character switches: intentionally no-op — keep original greeting.
 *
 * This function is that rule, extracted so both callers share one copy of it and
 * so it can be tested without a DOM.
 */
import type { TimeSlot } from './characterCopy'

export function withGreeting<T extends { id: string; content: string }>(
  messages: T[],
  greetingId: string,
  greeting: string,
): T[] {
  // Empty copy means the character has nothing authored for this locale. The
  // previous language is a worse greeting than the right one, and a far better
  // one than an empty bubble.
  if (!greeting.trim()) return messages

  // Anything below it makes this a transcript rather than an opening screen.
  if (messages.length !== 1) return messages
  if (messages[0].id !== greetingId) return messages

  // Same array when nothing changes: this feeds setMessages, and a fresh array
  // would re-render the whole transcript every time the locale is read.
  if (messages[0].content === greeting) return messages

  return [{ ...messages[0], content: greeting }]
}

/** The hour slot a greeting was built for, alongside the exact text that came
 *  out of it — always read together, from `characterCopy.ts`'s
 *  `getTimeSlot()` / `getGreetingForSlot()`. */
export interface CapturedGreeting {
  slot: TimeSlot
  text: string
}

/**
 * Rewrite the pristine greeting bubble to `candidate`, and report which
 * slot+text pair is now actually on screen.
 *
 * This is the one function allowed to move a caller's "captured" ref forward.
 * `candidate` is cheap to rebuild every render (`getTimeSlot()` plus a string
 * lookup) and callers may do so freely — but only a call into THIS function
 * may adopt it. When `withGreeting` declines to rewrite (not pristine, or the
 * text is already right), `captured` is handed back unchanged, byte-for-byte
 * the same object — so a key or an audio call downstream, built from
 * `captured.slot`, cannot drift out from under the text still on screen.
 *
 * This closes the bug where the displayed bubble was rewritten only on
 * avatar/locale switch, but the slot used to key the greeting's VOICE was
 * re-read from the live clock on every render — so a render that crossed an
 * hour boundary while the app sat pristine (e.g. the user starts typing) could
 * play the next slot's audio over the previous slot's still-displayed text.
 */
export function resolveGreeting<T extends { id: string; content: string }>(
  messages: T[],
  greetingId: string,
  candidate: CapturedGreeting,
  captured: CapturedGreeting | null,
): { messages: T[]; captured: CapturedGreeting } {
  const next = withGreeting(messages, greetingId, candidate.text)
  const moved = next !== messages
  return { messages: next, captured: moved || !captured ? candidate : captured }
}
