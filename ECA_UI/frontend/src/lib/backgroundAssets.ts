/**
 * Backdrop image index — the ONLY place that globs scene backgrounds.
 *
 * One folder per backdrop under `src/asset/backgrounds/<id>/`, one file with a
 * FIXED name: `background.{jpg,jpeg,png,webp}`. Anything else in the folder
 * (the original download, a .psd) is ignored and never reaches the bundle.
 * Folder ids are lowercase-hyphen: `house`.
 *
 * Whether the image is a flat picture or a 360° panorama is config
 * (`ENV_CONFIG.environment.background.kind`), not guessed from its shape.
 */

const MODULES = import.meta.glob('../asset/backgrounds/*/background.{jpg,jpeg,png,webp}', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>

function buildIndex(): Record<string, string> {
  const index: Record<string, string> = {}
  for (const [path, url] of Object.entries(MODULES)) {
    const match = /\/backgrounds\/([^/]+)\/background\.[a-z]+$/i.exec(path.replace(/\\/g, '/'))
    if (match) index[match[1]] = url
  }
  return index
}

export const BACKGROUNDS: Readonly<Record<string, string>> = buildIndex()

/** URL of a backdrop by folder id, or null when there is no such folder. */
export function resolveBackground(id: string | null | undefined): string | null {
  if (!id) return null
  return BACKGROUNDS[id] ?? null
}

/**
 * Texture repeat/offset that makes a flat image COVER a viewport: fill it
 * completely, keep the image's proportions, crop the overflow equally from
 * both sides. The CSS `background-size: cover` rule, as UV numbers.
 */
export function coverTransform(viewAspect: number, imageAspect: number): {
  repeat: [number, number]
  offset: [number, number]
} {
  if (!(viewAspect > 0) || !(imageAspect > 0)) return { repeat: [1, 1], offset: [0, 0] }
  if (viewAspect > imageAspect) {
    // View is wider than the image: use its full width, crop top and bottom.
    const ry = imageAspect / viewAspect
    return { repeat: [1, ry], offset: [0, (1 - ry) / 2] }
  }
  // View is taller (phone portrait): use its full height, crop the sides.
  const rx = viewAspect / imageAspect
  return { repeat: [rx, 1], offset: [(1 - rx) / 2, 0] }
}

// ── Which backdrop is active ─────────────────────────────────────────────

export type BackdropKind = 'dome' | 'flat' | 'panorama'

export interface BackdropSettings {
  kind: BackdropKind
  ground: { height: number; radius: number; yawDeg: number } | null
}

/**
 * What SceneEnvironment draws. 'dome' is procedural and needs no file; the
 * image kinds need a resolved image, and without one fall back to the gradient
 * rather than render nothing.
 */
export function backdropMode(bg: BackdropSettings, imageUrl: string | null): 'dome' | 'image' | 'gradient' {
  if (bg.kind === 'dome') return 'dome'
  return imageUrl ? 'image' : 'gradient'
}

/**
 * True when the backdrop supplies its own floor, so SceneLighting must not lay
 * the textured floor disc over it (it keeps the invisible shadow plane, so the
 * character's shadow still lands). The dome's lower half is its floor; a
 * grounded panorama projects the photo's floor.
 */
export function backdropOwnsFloor(bg: BackdropSettings, imageUrl: string | null): boolean {
  const mode = backdropMode(bg, imageUrl)
  if (mode === 'dome') return true
  return mode === 'image' && bg.kind === 'panorama' && bg.ground !== null
}
