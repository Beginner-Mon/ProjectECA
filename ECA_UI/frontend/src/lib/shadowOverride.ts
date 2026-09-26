/**
 * "Softer shadows" (Graphics settings; stored as `mtoon` for compatibility).
 *
 * The avatar is ALWAYS shaded with MToon — three-vrm builds every VRM material
 * as one, and nothing turns that off. This toggle only nudges two MToon values:
 *   - shadingShiftFactor: where light turns to shadow; higher = smaller shadows
 *   - shadeColorFactor:   the shadow tint; optional — null keeps each model's
 *                         own authored tint, which usually looks better than one
 *                         dark colour for every character
 *
 * The original values are stashed on the material the first time the override
 * is applied and put back when it is switched off. Before this, "off" simply
 * skipped the override and never restored anything, so the toggle could not be
 * turned off without reloading.
 */

import type * as THREE from 'three'

export interface SoftShadowSettings {
  shadingShiftFactor: number
  /** null = keep the model's own shade colour. */
  shadeColorHex: string | null
}

/** The parts of an MToonMaterial this touches — kept narrow so tests need no WebGL. */
interface MToonLike {
  isMToonMaterial?: boolean
  shadingShiftFactor: number
  shadeColorFactor: THREE.Color
  userData: Record<string, unknown>
}

const STASH = '__softShadowOriginal'

interface Original {
  shadingShiftFactor: number
  shadeColorFactor: THREE.Color
}

/** Apply or undo the override on one material. Returns true if it changed anything. */
export function applySoftShadows(mat: MToonLike, enabled: boolean, cfg: SoftShadowSettings): boolean {
  if (!mat.isMToonMaterial) return false
  const stashed = mat.userData[STASH] as Original | undefined

  if (enabled) {
    // Stash only once: a second "on" must not record the overridden values.
    if (!stashed) {
      mat.userData[STASH] = {
        shadingShiftFactor: mat.shadingShiftFactor,
        shadeColorFactor: mat.shadeColorFactor.clone(),
      } satisfies Original
    }
    mat.shadingShiftFactor = cfg.shadingShiftFactor
    if (cfg.shadeColorHex) mat.shadeColorFactor.set(cfg.shadeColorHex)
    return true
  }

  if (!stashed) return false
  mat.shadingShiftFactor = stashed.shadingShiftFactor
  mat.shadeColorFactor.copy(stashed.shadeColorFactor)
  delete mat.userData[STASH]
  return true
}
