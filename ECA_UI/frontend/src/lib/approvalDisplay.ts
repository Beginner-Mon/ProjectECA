/**
 * What an approval card warns about (stage 4, decision E11).
 *
 * The server sends the payload to show and nothing else: it does not say which
 * parts look dangerous. The client works that out here, from the exact text the
 * card prints, so the warning cannot disagree with what the user reads.
 *
 * Pure (vitest `node`, no DOM). Three checks:
 *   external_domains  every domain in an email address or URL in the payload;
 *   bidi              U+202A-U+202E or U+2066-U+2069 anywhere;
 *   homoglyph         a token that mixes Latin with Cyrillic or Greek letters,
 *                     or an `xn--` (punycode) label.
 */

export interface ApprovalField {
  label: string
  value: string
}

export interface ApprovalFlags {
  external_domains: string[]
  bidi: boolean
  homoglyph: boolean
}

const BIDI_CONTROLS = /[‪-‮⁦-⁩]/
const BIDI_CONTROLS_ALL = /[‪-‮⁦-⁩]/g

const EMAIL = /[^\s@<>"'()[\],;:]+@([\p{L}\p{N}-]+(?:\.[\p{L}\p{N}-]+)+)/gu
// scheme://[user@]host, and a bare www. host.
const URL_HOST = /\b[a-z][a-z0-9+.-]*:\/\/(?:[^\s/?#@]*@)?([^\s/?#:\\]+)/giu
const WWW_HOST = /(?:^|[\s(<"'])(www\.[\p{L}\p{N}.-]+)/giu

const TOKEN_SPLIT = /[^\p{L}\p{N}\p{M}-]+/u
const LATIN = /\p{Script=Latin}/u
const CYRILLIC = /\p{Script=Cyrillic}/u
const GREEK = /\p{Script=Greek}/u

/** Every text the card prints: labels, values and recipients. */
function allTexts(display: ApprovalField[], recipients: string[]): string[] {
  return [...display.flatMap((f) => [f.label, f.value]), ...recipients]
}

function cleanDomain(raw: string): string {
  return raw.toLowerCase().replace(/^\[|\]$/g, '').replace(/[.,;:!?)\]>'"]+$/, '')
}

/** Every domain named in an email address or URL, lower-cased, once each, in order of appearance. */
export function externalDomains(texts: string[]): string[] {
  const seen = new Set<string>()
  for (const text of texts) {
    const found: Array<{ at: number; host: string }> = []
    for (const re of [EMAIL, URL_HOST, WWW_HOST]) {
      for (const m of text.matchAll(re)) found.push({ at: m.index ?? 0, host: m[1] })
    }
    found.sort((a, b) => a.at - b.at)
    for (const { host } of found) {
      const domain = cleanDomain(host)
      if (domain) seen.add(domain)
    }
  }
  return [...seen]
}

export function hasBidi(texts: string[]): boolean {
  return texts.some((t) => BIDI_CONTROLS.test(t))
}

export function hasHomoglyph(texts: string[]): boolean {
  for (const text of texts) {
    for (const token of text.split(TOKEN_SPLIT)) {
      if (!token) continue
      if (/^xn--/i.test(token)) return true
      if (LATIN.test(token) && (CYRILLIC.test(token) || GREEK.test(token))) return true
    }
  }
  return false
}

export function approvalFlags(display: ApprovalField[], recipients: string[]): ApprovalFlags {
  const texts = allTexts(display, recipients)
  return {
    external_domains: externalDomains(texts),
    bidi: hasBidi(texts),
    homoglyph: hasHomoglyph(texts),
  }
}

export function hasAnyFlag(flags: ApprovalFlags): boolean {
  return flags.external_domains.length > 0 || flags.bidi || flags.homoglyph
}

/**
 * The text as the card prints it: each bidi control character is shown as a
 * visible `[U+202E]` marker instead of being obeyed, so it cannot reorder the
 * line the user is reading.
 */
export function visibleText(text: string): string {
  return text.replace(BIDI_CONTROLS_ALL, (c) => `[U+${c.charCodeAt(0).toString(16).toUpperCase().padStart(4, '0')}]`)
}
