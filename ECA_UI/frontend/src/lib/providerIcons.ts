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

/** Every preset id that has a logo file. */
export function providerIconIds(): string[] {
  return [...URL_BY_ID.keys()].sort()
}
