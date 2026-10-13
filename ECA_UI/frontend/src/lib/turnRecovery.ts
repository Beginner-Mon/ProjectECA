/**
 * Getting a reply back after its stream broke.
 *
 * A chat turn is one long SSE response. When it dies in the middle (network
 * drop, server process gone) the server has very often finished the turn and
 * saved it anyway — the user would only see the answer after a reload. So after
 * a failed turn ChatContext asks the server again, a few times, and swaps the
 * saved answer into the bubble.
 *
 * Pulled out of ChatContext for the same reason as lib/turnLifecycle.ts: the
 * decisions are testable here (vitest `node`, no DOM); the context only
 * supplies the side effects.
 */

import type { SessionMessage } from './api'

/** Waits before each attempt, counted from the failure: 3 s, 10 s, 30 s. */
export const RECOVERY_DELAYS_MS: readonly number[] = [3_000, 10_000, 30_000]

/**
 * The assistant message the server saved for the turn that sent `sentText`.
 *
 * Looks at the LATEST user message with exactly that text (the same words can
 * be sent twice) and takes the assistant message right after it. Nothing if the
 * server saved only the user's half, or if that reply is already on screen —
 * `onScreenIds` holds the server ids of the bubbles shown. A reply without an id
 * cannot be compared or given feedback on, so it does not count.
 */
export function pickSavedReply(
  messages: readonly SessionMessage[],
  sentText: string,
  onScreenIds: ReadonlySet<string>,
): SessionMessage | undefined {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'user' || messages[i].content !== sentText) continue
    const reply = messages[i + 1]
    if (!reply || reply.role !== 'assistant' || !reply.id) return undefined
    return onScreenIds.has(reply.id) ? undefined : reply
  }
  return undefined
}

export interface TurnRecoveryOptions<T> {
  /** Wait before each attempt, counted from creation. Ascending. */
  delays: readonly number[]
  /** One look at the server. Resolves to the reply, or nothing yet. A throw is a miss. */
  fetch: () => Promise<T | undefined | null>
  /** False once the user has left this conversation; ends the recovery. */
  isRelevant: () => boolean
  onRecovered: (found: T) => void
  /** Called once when the recovery ends for any reason. */
  onSettled?: () => void
}

export interface TurnRecovery {
  cancel: () => void
  /** Attempt now instead of waiting (the browser came back online). */
  tryNow: () => void
}

/** Schedules the attempts; stops at the first hit, the last delay, or when no longer relevant. */
export function createTurnRecovery<T>(opts: TurnRecoveryOptions<T>): TurnRecovery {
  const { delays, isRelevant } = opts
  let index = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let inFlight = false
  let settled = false

  const settle = () => {
    if (settled) return
    settled = true
    clearTimeout(timer)
    opts.onSettled?.()
  }

  // Gap to the next attempt, so the delays stay measured from the failure.
  const scheduleNext = () => {
    if (index >= delays.length) return settle()
    const gap = delays[index] - (index > 0 ? delays[index - 1] : 0)
    timer = setTimeout(attempt, gap)
  }

  async function attempt() {
    if (settled || inFlight) return
    clearTimeout(timer)
    if (!isRelevant()) return settle()
    inFlight = true
    index++
    let found: T | undefined | null
    try {
      found = await opts.fetch()
    } catch {
      found = undefined // offline or 5xx: the next attempt may do better
    }
    inFlight = false
    if (settled) return
    // The user may have moved on while the request was out.
    if (!isRelevant()) return settle()
    if (found !== undefined && found !== null) {
      opts.onRecovered(found)
      return settle()
    }
    scheduleNext()
  }

  scheduleNext()

  return {
    cancel: settle,
    tryNow: () => {
      void attempt()
    },
  }
}
