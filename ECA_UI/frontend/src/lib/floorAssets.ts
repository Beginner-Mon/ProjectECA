/**
 * Floor texture index — the ONLY place that globs floor textures.
 *
 * One folder per floor under `src/asset/floors/<id>/`, with FIXED file names so
 * every floor loads the same way whatever site it came from:
 *
 *   color.{jpg,png,webp}      required  base colour, sRGB
 *   normal.{jpg,png,webp}     optional  OpenGL convention (not DirectX)
 *   roughness.{jpg,png,webp}  optional  linear
 *   ao.{jpg,png,webp}         optional  ambient occlusion, linear
 *
 * Adding a floor = adding a folder. Anything else in the folder (previews,
 * .mtlx, displacement) is ignored, so a raw download never reaches the bundle.
 * Folder ids are lowercase-hyphen, like character slugs: `wood-ash`.
 */

const MODULES = import.meta.glob('../asset/floors/*/{color,normal,roughness,ao}.{jpg,jpeg,png,webp}', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>

export interface FloorTextures {
  color: string
  normal?: string
  roughness?: string
  ao?: string
}

type MapKind = keyof FloorTextures

function buildIndex(): Record<string, FloorTextures> {
  const partial: Record<string, Partial<FloorTextures>> = {}
  for (const [path, url] of Object.entries(MODULES)) {
    const match = /\/floors\/([^/]+)\/(color|normal|roughness|ao)\.[a-z]+$/i.exec(path.replace(/\\/g, '/'))
    if (!match) continue
    const [, id, kind] = match
    ;(partial[id] ??= {})[kind.toLowerCase() as MapKind] = url
  }
  const index: Record<string, FloorTextures> = {}
  for (const [id, maps] of Object.entries(partial)) {
    // A folder without a colour map is not a floor; skip rather than render grey.
    if (maps.color) index[id] = maps as FloorTextures
  }
  return index
}

export const FLOORS: Readonly<Record<string, FloorTextures>> = buildIndex()

/** The textures for a floor id, or null when there is no such folder. */
export function resolveFloor(id: string | null | undefined): FloorTextures | null {
  if (!id) return null
  return FLOORS[id] ?? null
}
