/**
 * The Ko-fi "support us" link (components/KofiButton.tsx).
 *
 * Configured, not hard-coded: VITE_KOFI_URL in .env.local / the Amplify build
 * env. Unset, blank or not a ko-fi.com page → no button at all, so a build
 * without a Ko-fi account shows nothing rather than a dead link.
 *
 * A plain link, deliberately — not Ko-fi's widget script, which would load
 * third-party JavaScript next to chat content in a health app.
 */

/** The Ko-fi page to open, or null when there is none to show. */
export function kofiUrl(raw: string | undefined): string | null {
  const value = raw?.trim()
  if (!value) return null
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return null
  }
  if (url.protocol !== 'https:') return null
  if (url.hostname !== 'ko-fi.com' && url.hostname !== 'www.ko-fi.com') return null
  if (url.pathname.length <= 1) return null // the bare home page, not a creator
  return url.toString()
}
