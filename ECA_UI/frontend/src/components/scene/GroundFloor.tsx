/**
 * GroundFloor — a visible, textured floor disc under the character.
 *
 * Replaces the invisible shadow-catching plane when `ENV_CONFIG.ground.floor.id`
 * names a folder under `src/asset/floors/` (see lib/floorAssets.ts). It receives
 * the same directional shadow the old plane caught, so nothing about the shadow
 * pipeline (light, ShadowCameraFitter) changes.
 *
 * Design points:
 *  - A small texture TILED across the disc, not one huge image: sharp at any
 *    size, ~0.8 MB for four 1K maps.
 *  - A DISC whose edge fades out, not the 200 m plane: a textured infinite plane
 *    shows a hard horizon. The fade is an alphaMap with its own (untiled) UV
 *    transform, so it blends into whatever background is behind it — gradient,
 *    theme, or the user's chosen colour.
 *  - Transparent because of that fade, so it draws in three's transparent pass.
 *    `renderOrder` below 0 puts it FIRST in that pass, so transparent parts of
 *    the avatar (MToon hair, lashes) still blend over it, not under it.
 *  - Lit by the existing light only. MToon allows one directional light; the
 *    floor uses a standard material under that same light plus the hemisphere
 *    fill. No new lights.
 */

import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import { useTexture } from '@react-three/drei'
import { useThree } from '@react-three/fiber'
import { ENV_CONFIG } from '../../config/environmentConfig'
import type { FloorTextures } from '../../lib/floorAssets'

/** White centre, fading to black (transparent) at the rim. alphaMap reads the green channel. */
function makeFadeTexture(fadeStart: number): THREE.CanvasTexture {
  const size = 256
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  if (ctx) {
    const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2)
    g.addColorStop(0, '#ffffff')
    g.addColorStop(Math.min(Math.max(fadeStart, 0), 0.99), '#ffffff')
    g.addColorStop(1, '#000000')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, size, size)
  }
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.NoColorSpace
  return tex
}

interface GroundFloorProps {
  textures: FloorTextures
}

export default function GroundFloor({ textures }: GroundFloorProps) {
  const { floor } = ENV_CONFIG.ground
  const gl = useThree((s) => s.gl)

  // useTexture takes a record of urls and returns the same keys; only pass the
  // maps this floor actually ships.
  const urls = useMemo(() => {
    const u: Record<string, string> = { map: textures.color }
    if (textures.normal) u.normalMap = textures.normal
    if (textures.roughness) u.roughnessMap = textures.roughness
    if (textures.ao) u.aoMap = textures.ao
    return u
  }, [textures])
  const maps = useTexture(urls) as Record<string, THREE.Texture>

  // Tile every map by the same amount so one repeat ≈ `metresPerTile` metres.
  // Circle UVs span 0..1 across the diameter.
  const repeat = (floor.radius * 2) / floor.metresPerTile
  useEffect(() => {
    const anisotropy = Math.min(8, gl.capabilities.getMaxAnisotropy())
    for (const [key, tex] of Object.entries(maps)) {
      tex.wrapS = THREE.RepeatWrapping
      tex.wrapT = THREE.RepeatWrapping
      tex.repeat.set(repeat, repeat)
      tex.anisotropy = anisotropy
      // The renderer runs without tone mapping: colour must be decoded as sRGB
      // or the wood washes out; data maps must NOT be, or they skew.
      tex.colorSpace = key === 'map' ? THREE.SRGBColorSpace : THREE.NoColorSpace
      tex.needsUpdate = true
    }
  }, [maps, repeat, gl])

  const fade = useMemo(() => makeFadeTexture(floor.fadeStart), [floor.fadeStart])
  useEffect(() => () => fade.dispose(), [fade])

  const normalScale = useMemo(
    () => new THREE.Vector2(floor.normalStrength, floor.normalStrength),
    [floor.normalStrength],
  )

  return (
    <mesh
      position={[floor.center[0], floor.center[1], 0]}
      receiveShadow
      renderOrder={-0.5}
    >
      {/* CircleGeometry lies in XY facing +Z: already right for this Z-up scene. */}
      <circleGeometry args={[floor.radius, 96]} />
      <meshStandardMaterial
        {...maps}
        color={floor.tint}
        normalScale={normalScale}
        roughness={1}
        metalness={0}
        aoMapIntensity={floor.aoIntensity}
        alphaMap={fade}
        transparent
      />
    </mesh>
  )
}
