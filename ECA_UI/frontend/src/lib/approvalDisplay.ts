/**
 * What an approval card warns about (stage 4, decision E11).
 *
 * The server sends the payload to show and nothing else: it does not say which
 * parts look dangerous. The client works that out here, from the exact text the
 * card prints, so the warning cannot disagree with what the user reads.
 *
 * Pure (vitest `node`, no DOM). Four checks, all run on the text with its
 * invisible characters removed, so a zero-width character cannot split a
 * domain out of sight:
 *   external_domains  every domain in an email address or URL in the payload;
 *   bidi              U+202A-U+202E or U+2066-U+2069 anywhere;
 *   invisible         any other format character (category Cf): zero-width,
 *                     soft hyphen, word joiner, BOM, tag characters;
 *   homoglyph         a token that mixes Latin with Cyrillic or Greek letters,
 *                     an `xn--` (punycode) label, or any domain label with a
 *                     non-ASCII letter (an all-Cyrillic look-alike).
 * `visibleText` prints every Cf character as `[U+XXXX]` (GĐ2 review M3).
 */

export interface ApprovalField {
  label: string
  value: string
}

export interface ApprovalFlags {
  external_domains: string[]
  bidi: boolean
  invisible: boolean
  homoglyph: boolean
}

const BIDI_CONTROLS = /[‪-‮⁦-⁩]/
// Every format character: bidi controls, zero-width, soft hyphen, word joiner,
// BOM, U+061C and the tag block.
const FORMAT_CHARS = /\p{Cf}/u
const FORMAT_CHARS_ALL = /\p{Cf}/gu

const EMAIL = /[^\s@<>"'()[\],;:]+@([\p{L}\p{N}_-]+(?:\.[\p{L}\p{N}_-]+)+)/gu
// scheme://[user@]host, and a bare www. host.
const URL_HOST = /\b[a-z][a-z0-9+.-]*:\/\/(?:[^\s/?#@]*@)?([^\s/?#:\\]+)/giu
const WWW_HOST = /(?:^|[\s(<"'])(www\.[\p{L}\p{N}.-]+)/giu
// M3: bare domain without scheme or @ (e.g. evil.example)
const BARE_DOMAIN = /(?:^|[\s(<"'])((?:[a-z0-9-]+\.)+[a-z]{2,})(?![\p{L}\p{N}-])/giu

const TOKEN_SPLIT = /[^\p{L}\p{N}\p{M}-]+/u
const LATIN = /\p{Script=Latin}/u
const CYRILLIC = /\p{Script=Cyrillic}/u
const GREEK = /\p{Script=Greek}/u

/** Every text the card prints: labels, values and recipients. */
function allTexts(display: ApprovalField[], recipients: string[]): string[] {
  return [...display.flatMap((f) => [f.label, f.value]), ...recipients]
}

/** The text with every format character removed: what detection reads. */
function stripFormat(text: string): string {
  return text.replace(FORMAT_CHARS_ALL, '')
}

function cleanDomain(raw: string): string {
  return raw.toLowerCase().replace(/^\[|\]$/g, '').replace(/[.,;:!?)\]>'"]+$/, '')
}

/** Every domain named in an email address or URL, lower-cased, once each, in order of appearance. */
export function externalDomains(texts: string[]): string[] {
  const seen = new Set<string>()
  for (const raw of texts) {
    const text = stripFormat(raw)
    const found: Array<{ at: number; host: string }> = []
    for (const re of [EMAIL, URL_HOST, WWW_HOST, BARE_DOMAIN]) {
      for (const m of text.matchAll(re)) found.push({ at: m.index ?? 0, host: m[1] ?? m[0] })
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

/** Any format character that is not a bidi control (those have their own flag). */
export function hasInvisible(texts: string[]): boolean {
  return texts.some((t) => [...t].some((c) => FORMAT_CHARS.test(c) && !BIDI_CONTROLS.test(c)))
}

const NON_ASCII = /\P{ASCII}/u

export function hasHomoglyph(texts: string[]): boolean {
  // A domain label with a non-ASCII letter looks like another domain; this
  // catches the all-Cyrillic look-alike that mixes nothing.
  if (externalDomains(texts).some((d) => NON_ASCII.test(d))) return true
  for (const raw of texts) {
    const text = stripFormat(raw)
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
    invisible: hasInvisible(texts),
    homoglyph: hasHomoglyph(texts),
  }
}

export function hasAnyFlag(flags: ApprovalFlags): boolean {
  return flags.external_domains.length > 0 || flags.bidi || flags.invisible || flags.homoglyph
}

/**
 * The text as the card prints it: each format character (bidi control,
 * zero-width, soft hyphen, tag character...) is shown as a visible `[U+XXXX]`
 * marker instead of being obeyed or hidden, so it cannot reorder or disguise
 * the line the user is reading.
 */
export function visibleText(text: string): string {
  return text.replace(
    FORMAT_CHARS_ALL,
    (c) => `[U+${(c.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, '0')}]`,
  )
}
