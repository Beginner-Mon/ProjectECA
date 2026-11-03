/**
 * Which images an assistant message may load (plan section 9, GĐ2 review H2).
 *
 * An image in markdown is fetched the moment the message renders, with no click
 * from the user: from any domain it is a beacon that can carry text in its URL.
 * So assistant markdown shows an image only when it comes from one of ECA's own
 * origins; any other image becomes a plain link that names its domain, and the
 * user decides whether to follow it.
 *
 * The allowed origins are the ones this app already loads its own files from:
 * the CDN of the character models (`ASSET_BASE`, CloudFront in front of S3) and
 * the API gateway (`API_GATEWAY`). See `ecaImageOrigins` in ChatMessage.
 *
 * Pure (vitest `node`, no DOM).
 */

export type ImageDecision =
  | { kind: 'image'; src: string }
  | { kind: 'link'; href: string; domain: string }
  | { kind: 'none' }

/** `scheme://host[:port]` of each base URL that parses; the rest are dropped. */
export function originsOf(bases: Array<string | undefined>): string[] {
  const out = new Set<string>()
  for (const base of bases) {
    if (!base) continue
    try {
      const url = new URL(base)
      if (url.protocol === 'https:' || url.protocol === 'http:') out.add(url.origin)
    } catch {
      // not a URL: not an origin
    }
  }
  return [...out]
}

/**
 * What to render for a markdown image. An absolute http(s) URL on an allowed
 * origin is an image. Any other absolute http(s) URL (including a
 * protocol-relative one, which is resolved against https) is a link showing its
 * domain. A relative or unparseable source, or another protocol, renders
 * nothing.
 */
export function decideImage(src: unknown, allowedOrigins: string[]): ImageDecision {
  if (typeof src !== 'string' || !src.trim()) return { kind: 'none' }
  let url: URL
  try {
    url = new URL(src.trim().startsWith('//') ? `https:${src.trim()}` : src.trim())
  } catch {
    return { kind: 'none' }
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return { kind: 'none' }
  if (allowedOrigins.includes(url.origin)) return { kind: 'image', src: url.href }
  return { kind: 'link', href: url.href, domain: url.hostname }
}
