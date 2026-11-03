import { describe, expect, it } from 'vitest'
import { approvalFlags, externalDomains, hasAnyFlag, hasBidi, hasHomoglyph, hasInvisible, visibleText } from './approvalDisplay'

describe('externalDomains', () => {
  it('finds the domain of an email address', () => {
    expect(externalDomains(['Send to Bob <bob@Example.com>'])).toEqual(['example.com'])
  })

  it('finds the host of a URL and ignores path, port and user info', () => {
    expect(externalDomains(['see https://user:pw@docs.example.org:8443/a/b?c=d#e'])).toEqual(['docs.example.org'])
  })

  it('finds a bare www host', () => {
    expect(externalDomains(['go to www.example.net/x'])).toEqual(['www.example.net'])
  })

  it('lists every domain once, in order of appearance, across all texts', () => {
    expect(
      externalDomains(['a@one.com and https://two.org/x', 'b@one.com', 'c@three.io.']),
    ).toEqual(['one.com', 'two.org', 'three.io'])
  })

  it('names nothing for text with no address or URL', () => {
    expect(externalDomains(['Buy milk at 5pm', 'v1.2.3'])).toEqual([])
  })

  it('keeps a non-Latin domain as written', () => {
    expect(externalDomains(['a@пример.рф'])).toEqual(['пример.рф'])
  })
})

describe('hasBidi', () => {
  it.each(['‪', '‫', '‬', '‭', '‮', '⁦', '⁧', '⁨', '⁩'])(
    'detects control %j',
    (c) => {
      expect(hasBidi([`abc${c}def`])).toBe(true)
    },
  )

  it('ignores ordinary text, Vietnamese and RTL letters without controls', () => {
    expect(hasBidi(['xin chào', 'שלום', 'a​b'])).toBe(false)
  })
})

describe('hasHomoglyph', () => {
  it('flags Latin mixed with Cyrillic in one token', () => {
    expect(hasHomoglyph(['pаypal.com'])).toBe(true) // Cyrillic a
  })

  it('flags Latin mixed with Greek in one token', () => {
    expect(hasHomoglyph(['gοogle'])).toBe(true) // Greek omicron
  })

  it('flags a punycode label', () => {
    expect(hasHomoglyph(['https://xn--pypal-4ve.com/login'])).toBe(true)
  })

  it('does not flag a text that uses two scripts in different tokens', () => {
    expect(hasHomoglyph(['hello привет', 'γειά σου friend'])).toBe(false)
  })

  it('does not flag Vietnamese or plain ASCII', () => {
    expect(hasHomoglyph(['Nhắc tôi uống thuốc lúc 8 giờ', 'pay@paypal.com'])).toBe(false)
  })
})

describe('approvalFlags', () => {
  it('reads labels, values and recipients', () => {
    const flags = approvalFlags(
      [{ label: 'Body', value: 'open https://evil.example/x' }],
      ['mia@corp.com'],
    )
    expect(flags).toEqual({ external_domains: ['evil.example', 'corp.com'], bidi: false, invisible: false, homoglyph: false })
    expect(hasAnyFlag(flags)).toBe(true)
  })

  it('is empty for a plain payload', () => {
    const flags = approvalFlags([{ label: 'Title', value: 'Take a walk' }], [])
    expect(flags).toEqual({ external_domains: [], bidi: false, invisible: false, homoglyph: false })
    expect(hasAnyFlag(flags)).toBe(false)
  })
})

describe('visibleText', () => {
  it('shows a bidi control as a marker instead of obeying it', () => {
    expect(visibleText('a‮b')).toBe('a[U+202E]b')
    expect(visibleText('⁦x⁩')).toBe('[U+2066]x[U+2069]')
  })

  it('leaves other text unchanged', () => {
    expect(visibleText('<b>hi</b> **x**')).toBe('<b>hi</b> **x**')
  })
})

describe('GĐ2 review M3: bypasses of the card flags', () => {
  it.each([
    ['a zero-width space', 'a@goo\u200Bgle.com', 'google.com'],
    ['a soft hyphen', 'a@exa\u00ADmple.com', 'example.com'],
    ['a word joiner', 'a@exa\u2060mple.com', 'example.com'],
    ['a BOM', 'a@exa\uFEFFmple.com', 'example.com'],
    ['a tag character', 'a@exa\u{E0041}mple.com', 'example.com'],
    ['an underscore', 'a@evil_corp.com', 'evil_corp.com'],
    ['a split URL host', 'see https://ev\u200Bil.example/x', 'evil.example'],
  ])('still names the domain split by %s', (_n, text, domain) => {
    expect(externalDomains([text])).toEqual([domain])
  })

  it('flags an all-Cyrillic look-alike domain that mixes nothing', () => {
    expect(hasHomoglyph(['a@\u0455\u0441\u043e\u0440\u0435.com'])).toBe(true)
    expect(hasHomoglyph(['https://\u0455\u0441\u043e\u0440\u0435.com/x'])).toBe(true)
  })

  it('still reads a Latin-only domain as clean', () => {
    expect(hasHomoglyph(['a@scope.com'])).toBe(false)
  })

  it('catches Latin mixed with Cyrillic even with a zero-width character beside it', () => {
    expect(hasHomoglyph(['p\u200B\u0430ypal'])).toBe(true)
  })

  it.each(['\u200B', '\u200C', '\u200D', '\u200E', '\u2060', '\u2064', '\uFEFF', '\u00AD', '\u061C', '\u{E0041}'])(
    'prints %j as a visible marker and flags it',
    (c) => {
      const marker = visibleText(`a${c}b`)
      expect(marker).toMatch(/^a\[U\+[0-9A-F]{4,5}\]b$/)
      expect(marker).not.toContain(c)
      expect(hasInvisible([`a${c}b`]) || hasBidi([`a${c}b`])).toBe(true)
    },
  )

  it('raises the invisible flag in approvalFlags and keeps ordinary text clean', () => {
    expect(approvalFlags([{ label: 'To', value: 'x\u200By' }], []).invisible).toBe(true)
    expect(approvalFlags([{ label: 'To', value: 'xin chào' }], []).invisible).toBe(false)
  })
})
