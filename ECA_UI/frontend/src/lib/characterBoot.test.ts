import { describe, expect, it, vi } from 'vitest'
import { bootCandidates, httpStatusOf, isNotFound, resolveInitialCharacter } from './characterBoot'

const http = (status: number) => Object.assign(new Error(`failed: ${status}`), { status })
type C = { slug: string; ok?: boolean }

function deps(over: Partial<Parameters<typeof resolveInitialCharacter<C>>[1]> = {}) {
  return {
    fetchOne: vi.fn(async (slug: string): Promise<C> => ({ slug })),
    fetchLites: vi.fn(async (): Promise<C[]> => []),
    isCompatible: (c: C) => c.ok !== false,
    aborted: () => false,
    ...over,
  }
}

describe('status helpers', () => {
  it('reads a numeric status only', () => {
    expect(httpStatusOf(http(503))).toBe(503)
    expect(httpStatusOf(new Error('x'))).toBeNull()
    expect(httpStatusOf(null)).toBeNull()
    expect(isNotFound(http(404))).toBe(true)
    expect(isNotFound(http(401))).toBe(false)
    expect(isNotFound(new TypeError('Failed to fetch'))).toBe(false)
  })
  it('candidates: saved pick then anne, no duplicate', () => {
    expect(bootCandidates('mei')).toEqual(['mei', 'anne'])
    expect(bootCandidates('anne')).toEqual(['anne'])
    expect(bootCandidates(null)).toEqual(['anne'])
  })
})

describe('resolveInitialCharacter', () => {
  it('takes the first candidate that exists', async () => {
    const d = deps()
    expect(await resolveInitialCharacter(['mei', 'anne'], d)).toEqual({ slug: 'mei' })
    expect(d.fetchOne).toHaveBeenCalledTimes(1)
  })

  it('moves to the next candidate on 404 only', async () => {
    const d = deps({
      fetchOne: vi.fn(async (slug: string) => {
        if (slug === 'mei') throw http(404)
        return { slug }
      }),
    })
    expect(await resolveInitialCharacter(['mei', 'anne'], d)).toEqual({ slug: 'anne' })
  })

  it.each([401, 500, 503])('stops on HTTP %i instead of trying the next one', async (status) => {
    const d = deps({ fetchOne: vi.fn(async () => { throw http(status) }) })
    await expect(resolveInitialCharacter(['mei', 'anne'], d)).rejects.toMatchObject({ status })
    expect(d.fetchOne).toHaveBeenCalledTimes(1)
    expect(d.fetchLites).not.toHaveBeenCalled()
  })

  it('stops on a network failure (no status)', async () => {
    const d = deps({ fetchOne: vi.fn(async () => { throw new TypeError('Failed to fetch') }) })
    await expect(resolveInitialCharacter(['anne'], d)).rejects.toThrow('Failed to fetch')
    expect(d.fetchLites).not.toHaveBeenCalled()
  })

  it('falls back to the catalog list when every candidate is 404', async () => {
    const d = deps({
      fetchOne: vi.fn(async (slug: string) => {
        if (slug === 'x') return { slug }
        throw http(404)
      }),
      fetchLites: vi.fn(async () => [{ slug: 'bad', ok: false }, { slug: 'x' }]),
    })
    expect(await resolveInitialCharacter(['anne'], d)).toEqual({ slug: 'x' })
  })

  it('a failing catalog list is an error, not a silent miss', async () => {
    const d = deps({
      fetchOne: vi.fn(async () => { throw http(404) }),
      fetchLites: vi.fn(async () => { throw http(502) }),
    })
    await expect(resolveInitialCharacter(['anne'], d)).rejects.toMatchObject({ status: 502 })
  })

  it('an empty catalog is an error', async () => {
    const d = deps({ fetchOne: vi.fn(async () => { throw http(404) }) })
    await expect(resolveInitialCharacter(['anne'], d)).rejects.toThrow('No characters available')
  })

  it('null when the picked catalog entry is itself 404, other failures throw', async () => {
    const d404 = deps({
      fetchOne: vi.fn(async () => { throw http(404) }),
      fetchLites: vi.fn(async () => [{ slug: 'a' }]),
    })
    expect(await resolveInitialCharacter(['anne'], d404)).toBeNull()

    let calls = 0
    const d500 = deps({
      fetchOne: vi.fn(async () => { throw (++calls === 1 ? http(404) : http(500)) }),
      fetchLites: vi.fn(async () => [{ slug: 'a' }]),
    })
    await expect(resolveInitialCharacter(['anne'], d500)).rejects.toMatchObject({ status: 500 })
  })
})
