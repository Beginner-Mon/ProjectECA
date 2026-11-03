import { describe, expect, it } from 'vitest'
import { decideImage, originsOf } from './markdownImages'

const ORIGINS = originsOf(['https://d111111abcdef8.cloudfront.net', 'https://api.example.com/prod/', undefined, 'not a url'])

describe('originsOf', () => {
  it('keeps the origin of each base and drops what is not a URL', () => {
    expect(ORIGINS).toEqual(['https://d111111abcdef8.cloudfront.net', 'https://api.example.com'])
  })
})

describe('decideImage', () => {
  it('lets an image from an ECA origin load', () => {
    expect(decideImage('https://d111111abcdef8.cloudfront.net/img/a.png', ORIGINS)).toEqual({
      kind: 'image',
      src: 'https://d111111abcdef8.cloudfront.net/img/a.png',
    })
  })

  it('turns a foreign-host image into a link that shows its domain', () => {
    expect(decideImage('https://evil.example/p.png?d=secret', ORIGINS)).toEqual({
      kind: 'link',
      href: 'https://evil.example/p.png?d=secret',
      domain: 'evil.example',
    })
  })

  it.each([
    'https://d111111abcdef8.cloudfront.net.evil.example/a.png',
    'https://evil.example/https://d111111abcdef8.cloudfront.net/a.png',
    'https://d111111abcdef8.cloudfront.net@evil.example/a.png',
    'http://d111111abcdef8.cloudfront.net/a.png',
    'https://d111111abcdef8.cloudfront.net:8443/a.png',
    '//evil.example/a.png',
  ])('does not treat %s as an ECA image', (src) => {
    expect(decideImage(src, ORIGINS).kind).toBe('link')
  })

  it('shows the real (ASCII) host of a look-alike domain', () => {
    const d = decideImage('https://\u0455\u0441\u043e\u0440\u0435.com/x.png', ORIGINS)
    expect(d).toMatchObject({ kind: 'link', domain: 'xn--e1argc3h.com' })
  })

  it.each([undefined, '', '   ', '/relative.png', 'javascript:alert(1)', 'data:image/png;base64,AAAA', 'ftp://a.example/x.png'])(
    'renders nothing for %j',
    (src) => {
      expect(decideImage(src, ORIGINS).kind).toBe('none')
    },
  )

  it('allows nothing when no origin is configured', () => {
    expect(decideImage('https://d111111abcdef8.cloudfront.net/a.png', []).kind).toBe('link')
  })
})
