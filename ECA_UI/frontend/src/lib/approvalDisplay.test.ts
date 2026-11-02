import { describe, expect, it } from 'vitest'
import { approvalFlags, externalDomains, hasAnyFlag, hasBidi, hasHomoglyph, visibleText } from './approvalDisplay'

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
    expect(flags).toEqual({ external_domains: ['evil.example', 'corp.com'], bidi: false, homoglyph: false })
    expect(hasAnyFlag(flags)).toBe(true)
  })

  it('is empty for a plain payload', () => {
    const flags = approvalFlags([{ label: 'Title', value: 'Take a walk' }], [])
    expect(flags).toEqual({ external_domains: [], bidi: false, homoglyph: false })
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
