/**
 * What ECA remembers about the user — the decisions behind the Memory screen.
 *
 * Pulled out of MemoryContent for the same reason as lib/turnRecovery.ts: the
 * rules are testable here (vitest `node`, no DOM); the component only supplies
 * the side effects.
 */

import type { UserMemoryItem, UserMemoryWire } from './api'

/** The server rejects a fact longer than this (UserMemoryCreate.fact_text). */
export const MAX_FACT_LENGTH = 500

/**
 * The list as the screen wants it. An older backend sends neither `source` nor
 * `updated_at`: a fact without a source was typed by the user, and one without
 * an update time was last touched when it was made.
 */
export function normaliseFacts(answer: { facts?: unknown } | null | undefined): UserMemoryItem[] {
  const facts = answer?.facts
  if (!Array.isArray(facts)) return []
  return (facts as UserMemoryWire[]).map(normaliseFact)
}

export function normaliseFact(f: UserMemoryWire): UserMemoryItem {
  return {
    id: f.id,
    fact_text: f.fact_text,
    category: f.category ?? null,
    source: f.source === 'extracted' ? 'extracted' : 'user',
    created_at: f.created_at,
    updated_at: f.updated_at || f.created_at,
  }
}

/** The text to send for a new fact, or null if it must not be sent. */
export function validateNewFact(input: string): string | null {
  const text = input.trim()
  if (text.length < 1 || Array.from(text).length > MAX_FACT_LENGTH) return null
  return text
}

/** Most recently updated first. Returns a new array. */
export function sortFacts(facts: readonly UserMemoryItem[]): UserMemoryItem[] {
  const at = (f: UserMemoryItem) => {
    const t = Date.parse(f.updated_at)
    return Number.isNaN(t) ? 0 : t
  }
  return [...facts].sort((a, b) => at(b) - at(a))
}
