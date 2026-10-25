/**
 * Which character the app boots with, and what a failure while finding one means.
 *
 * Pure (vitest `node`): the fetches are passed in. The rule that used to be
 * buried in MotionContext's catch blocks lives here so it can be tested:
 *
 *   404        "that character does not exist" -> try the next candidate.
 *   anything   the server or the network is the problem, and the next
 *   else       candidate would fail the same way -> stop and say so.
 *
 * Before this, every error was treated as a 404. With the backend down all
 * candidates "missed", `character` stayed null, no error was set, and the
 * viewer spun on "Loading characters..." for good.
 */

/** HTTP status carried by an error thrown from lib/characters.ts, if any. */
export function httpStatusOf(err: unknown): number | null {
  const s = (err as { status?: unknown } | null | undefined)?.status
  return typeof s === 'number' ? s : null
}

/** Only a clean 404 means "not there, try another". */
export function isNotFound(err: unknown): boolean {
  return httpStatusOf(err) === 404
}

/** The slugs to try in order: the user's saved pick (if any), then Anne. */
export function bootCandidates(syncedSlug: string | null | undefined): string[] {
  return syncedSlug && syncedSlug !== 'anne' ? [syncedSlug, 'anne'] : ['anne']
}

export interface ResolveCharacterDeps<C extends { slug: string }> {
  fetchOne: (slug: string) => Promise<C>
  fetchLites: () => Promise<C[]>
  isCompatible: (c: C) => boolean
  aborted: () => boolean
}

/**
 * The character to boot with, or null when the catalog answered but none of
 * the candidates could be opened (all 404). Throws on any other failure, and
 * when the catalog itself is empty — the caller turns the throw into the error
 * state the viewer already knows how to show.
 */
export async function resolveInitialCharacter<C extends { slug: string }>(
  candidates: string[],
  deps: ResolveCharacterDeps<C>,
): Promise<C | null> {
  for (const slug of candidates) {
    try {
      return await deps.fetchOne(slug)
    } catch (err) {
      if (deps.aborted() || !isNotFound(err)) throw err
      // 404 -> next candidate
    }
  }
  // Last resort: the catalog list, picking the first compatible entry.
  const lites = await deps.fetchLites()
  if (lites.length === 0) throw new Error('No characters available')
  const pick = lites.find((c) => deps.isCompatible(c)) ?? lites[0]
  try {
    return await deps.fetchOne(pick.slug)
  } catch (err) {
    if (deps.aborted() || !isNotFound(err)) throw err
    return null
  }
}
