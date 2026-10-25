/**
 * What ECA remembers about the user — the decisions behind the Memory screen.
 *
 * Pulled out of MemoryContent for the same reason as lib/turnRecovery.ts: the
 * rules are testable here (vitest `node`, no DOM); the component only supplies
 * the side effects.
 */

import { MEMORY_TYPES, type MemoryType, type UserMemoryItem, type UserMemoryWire } from './api'

/** The server rejects a fact longer than this (UserMemoryCreate.fact_text). */
export const MAX_FACT_LENGTH = 500

/**
 * The list as the screen wants it. An older backend sends neither `source` nor
 * `updated_at`: a fact without a source was typed by the user, and one without
 * an update time was last touched when it was made. A backend from before the
 * tiers change sends no `kind`, `tier`, `status`, `project` or `said_at`.
 */
export function normaliseFacts(answer: { facts?: unknown } | null | undefined): UserMemoryItem[] {
  const facts = answer?.facts
  if (!Array.isArray(facts)) return []
  return (facts as UserMemoryWire[]).map(normaliseFact)
}

export function normaliseFact(f: UserMemoryWire): UserMemoryItem {
  const kind = f.kind === 'instruction' ? 'instruction' : 'fact'
  const known = MEMORY_TYPES.find((type) => type === f.memory_type)
  return {
    id: f.id,
    fact_text: f.fact_text,
    category: f.category ?? null,
    kind,
    tier: f.tier === 1 ? 1 : 2,
    status: f.status === 'candidate' ? 'candidate' : 'active',
    project: f.project ?? null,
    memory_type: known ?? (kind === 'instruction' ? 'instruction' : 'profile'),
    source: f.source === 'extracted' ? 'extracted' : 'user',
    created_at: f.created_at,
    updated_at: f.updated_at || f.created_at,
    said_at: f.said_at ?? f.created_at,
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

export interface TopicGroup {
  topic: string | null
  items: UserMemoryItem[]
}

export interface TypeGroup {
  type: MemoryType
  topics: TopicGroup[]
}

/**
 * The Memory screen: one section per memory type in a fixed order (empty ones
 * left out), topics alphabetical with "no topic" last, newest first inside a
 * topic. Status is ignored: an older backend's candidate is shown like any
 * other row.
 */
export function groupByType(facts: readonly UserMemoryItem[]): TypeGroup[] {
  const sorted = sortFacts(facts)
  return MEMORY_TYPES.flatMap((type) => {
    const byTopic = new Map<string | null, UserMemoryItem[]>()
    for (const fact of sorted) {
      if (fact.memory_type !== type) continue
      const key = fact.project ?? null
      const list = byTopic.get(key)
      if (list) list.push(fact)
      else byTopic.set(key, [fact])
    }
    if (byTopic.size === 0) return []
    const topics = [...byTopic.entries()]
      .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : a.localeCompare(b)))
      .map(([topic, items]) => ({ topic, items }))
    return [{ type, topics }]
  })
}

export const MAX_TOPIC_WORDS = 4
export const MAX_TOPIC_LENGTH = 60

/** The topic to send (trimmed, spaces collapsed), null for none, 'invalid' if the server would answer 422. */
export function validateTopic(input: string): string | null | 'invalid' {
  const topic = input.trim().replace(/\s+/g, ' ')
  if (topic === '') return null
  if (topic.split(' ').length > MAX_TOPIC_WORDS || Array.from(topic).length > MAX_TOPIC_LENGTH) return 'invalid'
  return topic
}
