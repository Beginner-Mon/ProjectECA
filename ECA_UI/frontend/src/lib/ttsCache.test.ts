import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  STATIC_CACHE_TTL_MS,
  TTS_CACHE_TTL_MS,
  cacheKeyFor,
  evictionIds,
  isExpired,
  sha256Hex,
  staleIds,
  sweepIds,
  ttlOf,
  voiceScope,
  type CacheMeta,
} from './ttsCache'

/*
 * The decisions only. The IndexedDB half is not exercised here: vitest runs in
 * `node`, and the project has no IndexedDB fake (adding one would be a new
 * dependency). Every IDB call goes through one of these functions to decide
 * what to read or delete, so this is where the logic that can be wrong lives.
 */

function meta(p: Partial<CacheMeta> & { id: string }): CacheMeta {
  return {
    sub: 'user-a',
    persona: 'anne',
    scope: 'anne',
    voiceVersion: 'v1',
    codec: 'opus',
    sampleRate: 48000,
    createdAt: 0,
    bytes: 1,
    ...p,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('cacheKeyFor', () => {
  it('is the SHA-256 hex of the JSON pair [text, persona]', async () => {
    // Computed outside the app: printf '%s' '["hi","anne"]' | sha256sum
    expect(await cacheKeyFor('hi', 'anne')).toBe(
      '1f6161e69619a052596d01596229dfd613b0b4b303a13f0d8c3a3d8cba592120',
    )
  })

  it('is stable, and handles non-ASCII text', async () => {
    const a = await cacheKeyFor('Xin chào, tôi là Anne.', 'anne')
    expect(a).toMatch(/^[0-9a-f]{64}$/)
    expect(await cacheKeyFor('Xin chào, tôi là Anne.', 'anne')).toBe(a)
  })

  it('differs by character: the same words in another voice are another clip', async () => {
    expect(await cacheKeyFor('hi', 'anne')).not.toBe(await cacheKeyFor('hi', 'miki'))
  })

  it('cannot be confused by a separator inside the text', async () => {
    // A plain `${text}|${persona}` join would make these two the same string.
    expect(await cacheKeyFor('a|b', 'c')).not.toBe(await cacheKeyFor('a', 'b|c'))
  })

  it('gives no key — so no cache — where SubtleCrypto does not exist', async () => {
    // Insecure origins (http:// on a LAN address) have no crypto.subtle.
    vi.stubGlobal('crypto', {})
    expect(await cacheKeyFor('hi', 'anne')).toBeNull()
  })
})

describe('isExpired', () => {
  const t0 = 1_700_000_000_000

  it('uses a one-day TTL', () => {
    expect(TTS_CACHE_TTL_MS).toBe(24 * 60 * 60 * 1000)
  })

  it('keeps a row younger than the TTL', () => {
    expect(isExpired(t0, t0)).toBe(false)
    expect(isExpired(t0, t0 + TTS_CACHE_TTL_MS - 1)).toBe(false)
  })

  it('expires a row at exactly the TTL', () => {
    expect(isExpired(t0, t0 + TTS_CACHE_TTL_MS)).toBe(true)
  })

  it('expires a row dated in the future — the clock was moved back', () => {
    expect(isExpired(t0 + 60_000, t0)).toBe(true)
  })

  it('expires a row with a corrupt timestamp', () => {
    expect(isExpired(Number.NaN, t0)).toBe(true)
  })
})

describe('staleIds', () => {
  const rows = [
    meta({ id: 'a-old', voiceVersion: 'old' }),
    meta({ id: 'a-new', voiceVersion: 'new' }),
    meta({ id: 'a-miki', persona: 'miki', scope: 'miki', voiceVersion: 'old' }),
    meta({ id: 'b-old', sub: 'user-b', voiceVersion: 'old' }),
  ]

  it("drops this user's rows for this character made with another recording", () => {
    // Leaves the other character, and the other user — whose rows are the
    // sign-in sweep's business, not this one's.
    expect(staleIds(rows, 'user-a', 'anne', 'new')).toEqual(['a-old'])
  })

  it('does nothing when the server did not say which recording it used', () => {
    expect(staleIds(rows, 'user-a', 'anne', '')).toEqual([])
  })

  it('keeps the other language when the event names its language', () => {
    const bilingual = [
      meta({ id: 'vi', scope: voiceScope('anne', 'vi'), voiceVersion: 'hash-vi' }),
      meta({ id: 'en', scope: voiceScope('anne', 'en'), voiceVersion: 'hash-en' }),
    ]
    expect(staleIds(bilingual, 'user-a', voiceScope('anne', 'en'), 'hash-en')).toEqual([])
  })

  it('without a language, reads the other language as stale (known cost, see voiceScope)', () => {
    const vi = [meta({ id: 'vi', voiceVersion: 'hash-vi' })]
    expect(staleIds(vi, 'user-a', voiceScope('anne'), 'hash-en')).toEqual(['vi'])
  })
})

describe('sweepIds', () => {
  const now = 10 * TTS_CACHE_TTL_MS

  it("removes every other user's rows, and this user's expired ones", () => {
    const rows = [
      meta({ id: 'mine-fresh', createdAt: now - 1000 }),
      meta({ id: 'mine-old', createdAt: now - TTS_CACHE_TTL_MS }),
      meta({ id: 'theirs-fresh', sub: 'user-b', createdAt: now }),
    ]
    expect(sweepIds(rows, 'user-a', now).sort()).toEqual(['mine-old', 'theirs-fresh'])
  })

  it('with nobody signed in, removes everything', () => {
    expect(sweepIds([meta({ id: 'x', createdAt: now })], null, now)).toEqual(['x'])
  })
})

describe('evictionIds', () => {
  it('keeps everything at or under the cap', () => {
    expect(evictionIds([meta({ id: 'a' }), meta({ id: 'b' })], 2)).toEqual([])
  })

  it('evicts the oldest first', () => {
    const rows = [
      meta({ id: 'mid', createdAt: 2 }),
      meta({ id: 'old', createdAt: 1 }),
      meta({ id: 'new', createdAt: 3 }),
    ]
    expect(evictionIds(rows, 2)).toEqual(['old'])
    expect(evictionIds(rows, 1)).toEqual(['old', 'mid'])
  })

  it('never counts static rows towards the turn cap (T7)', () => {
    // 101 turns plus one greeting: the greeting survives, the oldest turn goes.
    const turns = Array.from({ length: 101 }, (_, i) => meta({ id: `turn-${i}`, createdAt: i }))
    const rows = [...turns, meta({ id: 'greeting', kind: 'static', createdAt: 10_000 })]
    expect(evictionIds(rows, 100)).toEqual(['turn-0'])
  })
})

describe('static entries (T7)', () => {
  const DAY = 24 * 60 * 60 * 1000

  it('static TTL is 30 days', () => {
    expect(STATIC_CACHE_TTL_MS).toBe(30 * DAY)
  })

  it('ttlOf prefers the row override, then the kind default', () => {
    expect(ttlOf({})).toBe(TTS_CACHE_TTL_MS)
    expect(ttlOf({ kind: 'turn' })).toBe(TTS_CACHE_TTL_MS)
    expect(ttlOf({ kind: 'static' })).toBe(STATIC_CACHE_TTL_MS)
    expect(ttlOf({ kind: 'static', ttlMs: 123 })).toBe(123)
  })

  it('a 29-day-old static row is kept, a 31-day-old one is swept', () => {
    const now = 100 * DAY
    const rows = [
      meta({ id: 'static-29d', kind: 'static', createdAt: now - 29 * DAY }),
      meta({ id: 'static-31d', kind: 'static', createdAt: now - 31 * DAY }),
    ]
    expect(sweepIds(rows, 'user-a', now)).toEqual(['static-31d'])
  })

  it('a 2-day-old turn is still swept while the static row lives on', () => {
    const now = 100 * DAY
    const rows = [
      meta({ id: 'turn-2d', createdAt: now - 2 * DAY }),
      meta({ id: 'static-2d', kind: 'static', createdAt: now - 2 * DAY }),
    ]
    expect(sweepIds(rows, 'user-a', now)).toEqual(['turn-2d'])
  })

  it("other users' static rows are swept like everything else", () => {
    const now = 100 * DAY
    const rows = [meta({ id: 'theirs', sub: 'user-b', kind: 'static', createdAt: now })]
    expect(sweepIds(rows, 'user-a', now)).toEqual(['theirs'])
  })

  it('sha256Hex matches the precomputed Vietnamese digest (contract E)', async () => {
    // printf '%s' 'Chào buổi sáng! Hôm nay mình giúp gì cho bạn?' | sha256sum
    expect(await sha256Hex('Chào buổi sáng! Hôm nay mình giúp gì cho bạn?')).toBe(
      '6d4d8e320d2a1eb08dbea2ba55ffeedb038eaaf013a29c7e59899efaae6b3939',
    )
  })
})
