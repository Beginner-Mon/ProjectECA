/**
 * Keep the user's camera inside the scene (03-10).
 *
 * A pan moves the orbit point AND the camera, without limit. maxDistance only
 * caps the distance between the two, so a long pan carried both straight out
 * through the stage dome (radius 30 m) into empty space (Owner). Two bounds:
 *
 * - the orbit point stays within PAN_RADIUS of the character, horizontally,
 *   and between floor and a little above head height — panning "hits a wall"
 *   and slides along it rather than snapping back;
 * - the camera itself stays inside the dome, as a backstop for the angles
 *   the first bound alone does not cover.
 *
 * Z-up world (the floor is z = 0).
 */

import * as THREE from 'three'

/** How far the orbit point may be panned from the character, metres. */
export const PAN_RADIUS = 4
/** Orbit point height range, metres: floor to a little above the head. */
export const PAN_Z_MIN = 0
export const PAN_Z_MAX = 2.5
/** Keep the camera this far inside the dome wall, metres. */
export const DOME_MARGIN = 1

/**
 * The shift to apply to BOTH the orbit point and the camera so the orbit point
 * is back inside its bounds. Zero when it already is. Applying the same shift
 * to both keeps the view's angle and distance — only the panning stops.
 * Writes into and returns `out`.
 */
export function panCorrection(target: THREE.Vector3, character: THREE.Vector3, out: THREE.Vector3): THREE.Vector3 {
  out.set(0, 0, 0)
  const dx = target.x - character.x
  const dy = target.y - character.y
  const d = Math.hypot(dx, dy)
  if (d > PAN_RADIUS) {
    const k = (d - PAN_RADIUS) / d
    out.x = -dx * k
    out.y = -dy * k
  }
  if (target.z < PAN_Z_MIN) out.z = PAN_Z_MIN - target.z
  else if (target.z > PAN_Z_MAX) out.z = PAN_Z_MAX - target.z
  return out
}

/**
 * Pull `pos` straight back toward `centre` if it is further than `radius`.
 * @returns true when it moved.
 */
export function keepInsideSphere(pos: THREE.Vector3, centre: THREE.Vector3, radius: number): boolean {
  const d = pos.distanceTo(centre)
  if (d <= radius || d === 0) return false
  pos.sub(centre).multiplyScalar(radius / d).add(centre)
  return true
}
