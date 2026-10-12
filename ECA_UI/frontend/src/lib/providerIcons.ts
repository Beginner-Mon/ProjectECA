/**
 * Brand marks for the BYOK provider cards.
 *
 * One SVG per provider in `asset/provider-icons/`, named after the preset id
 * it belongs to (`openai.svg`, `gemini.svg`, …) — so giving a provider its
 * logo is adding a file, and nothing here maps names by hand. The files come
 * from @lobehub/icons-static-svg; see the LICENSE beside them.
 *
 * A preset with no file has no logo in that set and falls back to a neutral
 * icon. Showing another company's mark in its place would be the very mistake
 * this module exists to remove.
 *
 * These files cover the hand-written presets only. A provider read from the
 * catalog brings its logo with it: see catalogLogoUrl.
 */

const FILES = import.meta.glob<string>('../asset/provider-icons/*.svg', {
  eager: true,
  query: '?url',
  import: 'default',
})

const URL_BY_ID = new Map<string, string>(
  Object.entries(FILES).map(([path, url]) => [path.slice(path.lastIndexOf('/') + 1, -'.svg'.length), url]),
)

/** The logo's URL for a preset id, or undefined when the provider has none. */
export function providerIconUrl(presetId: string): string | undefined {
  return URL_BY_ID.get(presetId)
}

/**
 * The catalog's own logo for one of its providers. models.dev serves one per
 * provider id (a generic mark when the provider gave none), as a single-colour
 * SVG with open CORS, which is what the mask in ProviderIcon needs.
 */
export function catalogLogoUrl(catalogId: string): string {
  return `https://models.dev/logos/${encodeURIComponent(catalogId)}.svg`
}

/** Every preset id that has a logo file. */
export function providerIconIds(): string[] {
  return [...URL_BY_ID.keys()].sort()
}
