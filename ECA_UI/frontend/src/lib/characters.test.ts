import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchAvatarProfile, fetchCharacter, fetchCharacters, fetchClips } from './characters'
import { authHeader } from './api'

// The token comes from the ONE authHeader() in lib/api.ts — mocked here so
// these tests assert the wiring (header attached / not attached), not Amplify.
vi.mock('./api', () => ({
  authHeader: vi.fn(async () => ({ Authorization: 'Bearer test-token' })),
  cognitoSub: vi.fn(async () => 'user-test'),
}))

/*
 * Suite runs in vitest's `node` environment: `fetch` is stubbed per test.
 */

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('fetchCharacters (list)', () => {
  it('sends no token — the grid is public', async () => {
    const seen: RequestInit[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: unknown, init?: RequestInit) => {
        seen.push(init ?? {})
        return jsonResponse({ characters: [], total: 0 })
      }),
    )

    await fetchCharacters()

    // No init at all — certainly no Authorization header.
    expect(seen[0]?.headers ?? {}).toEqual({})
    expect(authHeader).not.toHaveBeenCalled()
  })
})

describe('fetchCharacter / fetchAvatarProfile (detail)', () => {
  it('attaches the Cognito token (T2)', async () => {
    const seen: Array<{ href: string; init?: RequestInit }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown, init?: RequestInit) => {
        const href = typeof input === 'string' ? input : String(input)
        seen.push({ href, init })
        if (href.includes('avatar-profile')) return jsonResponse({ recipes: [] })
        return jsonResponse({ slug: 'anne' })
      }),
    )

    await fetchCharacter('anne')
    await fetchAvatarProfile('anne')

    expect(seen).toHaveLength(2)
    for (const { init } of seen) {
      expect((init?.headers ?? {}) as Record<string, string>).toMatchObject({
        Authorization: 'Bearer test-token',
      })
    }
  })
})

describe('fetchClips (/audio)', () => {
  it('calls /audio with repeated clip params, lang, and the token', async () => {
    const seen: Array<{ href: string; init?: RequestInit }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: unknown, init?: RequestInit) => {
        seen.push({ href: String(input), init })
        return jsonResponse({ clips: {} })
      }),
    )

    const clips = await fetchClips('anne', ['greeting.morning', 'greeting.evening'], 'vi')

    expect(clips).toEqual({})
    const { href, init } = seen[0]
    expect(href).toContain('/characters/anne/audio?')
    expect(href).toContain('clip=greeting.morning')
    expect(href).toContain('clip=greeting.evening')
    expect(href).toContain('lang=vi')
    expect((init?.headers ?? {}) as Record<string, string>).toMatchObject({
      Authorization: 'Bearer test-token',
    })
  })

  it('throws on a non-2xx so the caller stays text-only', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))

    await expect(fetchClips('anne', ['greeting.morning'], 'vi')).rejects.toThrow('401')
  })
})
