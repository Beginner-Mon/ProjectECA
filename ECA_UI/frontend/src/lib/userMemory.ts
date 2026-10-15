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
 * an update time was last touched when it was made. A backend from before the
 * tiers change sends no `kind`, `tier`, `status`, `project` or `said_at`.
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
    kind: f.kind === 'instruction' ? 'instruction' : 'fact',
    tier: f.tier === 1 ? 1 : 2,
    status: f.status === 'candidate' ? 'candidate' : 'active',
    project: f.project ?? null,
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

export interface ProjectGroup {
  project: string | null
  items: UserMemoryItem[]
}

export interface GroupedFacts {
  /** Standing instructions ECA follows everywhere (tier 1). */
  instructions: UserMemoryItem[]
  /** What ECA knows, grouped by project label (null = no label). */
  byProject: ProjectGroup[]
  /** Things ECA noticed but has not confirmed yet (candidates). */
  candidates: UserMemoryItem[]
}

/** Split the list into the three sections of the Memory screen. */
export function groupFacts(facts: readonly UserMemoryItem[]): GroupedFacts {
  const instructions: UserMemoryItem[] = []
  const candidates: UserMemoryItem[] = []
  const groups = new Map<string | null, UserMemoryItem[]>()
  for (const fact of facts) {
    if (fact.status === 'candidate') {
      candidates.push(fact)
    } else if (fact.kind === 'instruction' && fact.tier === 1) {
      instructions.push(fact)
    } else {
      const key = fact.project ?? null
      const list = groups.get(key)
      if (list) list.push(fact)
      else groups.set(key, [fact])
    }
  }
  const byProject: ProjectGroup[] = [...groups.entries()]
    .sort(([a], [b]) => {
      if (a === null) return 1
      if (b === null) return -1
      return a.localeCompare(b)
    })
    .map(([project, items]) => ({ project, items }))
  return { instructions, byProject, candidates }
}
