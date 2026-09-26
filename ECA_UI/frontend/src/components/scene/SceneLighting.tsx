/**
 * SceneLighting — Phase 2 (Lighting) + Phase 3 (Shadows) + Phase 4 (Ground)
 *
 * MToon-safe lighting: exactly ONE directional light for toon-shading (NdotL),
 * plus a hemisphere light for ambient fill. No multi-light PBR setup.
 *
 * The directional light also carries the shadow configuration: PCFSoft shadow
 * map, tight frustum, tuned bias values.
 *
 * Includes the ground plane (invisible shadow receiver). That plane is the ONLY
 * floor shadow: drei's <ContactShadows> was removed on 25/09. It was mounted
 * under a -PI/2 wrapper, which pointed both its plane and its capture camera
 * DOWN, so it never rendered from above and showed as a black square when the
 * camera went under the floor, while still costing a full extra scene render
 * plus two blur passes every frame. If a soft contact shadow is wanted again,
 * the wrapper must be +PI/2 (drei is Y-up, this scene is Z-up), and it needs
 * visual tuning, since nobody has ever seen it rendered.
 */

import { useRef, useEffect, Suspense } from 'react'
import * as THREE from 'three'
import { useFrame } from '@react-three/fiber'
import type { VRM } from '@pixiv/three-vrm'
import { ENV_CONFIG } from '../../config/environmentConfig'
import { DEFAULT_SHADOW_FIT, ShadowCameraFitter } from '../../lib/shadowFit'
import { resolveFloor } from '../../lib/floorAssets'
import { backdropOwnsFloor, resolveBackground } from '../../lib/backgroundAssets'
import GroundFloor from './GroundFloor'

// Some backdrops bring their own floor: the stage dome (its lower half) and a
// grounded panorama (the photo's floor, curving up into its walls). Our floor
// disc would be laid over it, so it stands down while one is active; the
// invisible shadow plane stays, so the character still casts a shadow there.
const { background } = ENV_CONFIG.environment
const GROUNDED_BACKDROP = backdropOwnsFloor(
  background,
  background.kind === 'dome' ? null : resolveBackground(background.id),
)

// Resolved once: the floor id is config, not state.
const FLOOR_TEXTURES = GROUNDED_BACKDROP ? null : resolveFloor(ENV_CONFIG.ground.floor.id)
if (ENV_CONFIG.ground.floor.id && !GROUNDED_BACKDROP && !FLOOR_TEXTURES) {
  console.warn(`[floor] no textures for "${ENV_CONFIG.ground.floor.id}" under src/asset/floors/ — using the invisible shadow plane`)
}

interface SceneLightingProps {
  vrm: VRM | null
}

export default function SceneLighting({ vrm }: SceneLightingProps) {
  const lightRef = useRef<THREE.DirectionalLight>(null!)
  const fitterRef = useRef<ShadowCameraFitter | null>(null)

  const {
    lighting: { main, ambient },
    shadows,
    ground,
  } = ENV_CONFIG

  // ── Configure shadow map + build the auto-fitter ──────────────────────
  // The frustum extents are NOT set here any more: they are derived from the
  // subject every frame (see lib/shadowFit.ts). A fixed frustum centred on the
  // world origin is what made shadows clip in a straight line while wasting
  // ~92% of the shadow map.
  useEffect(() => {
    const light = lightRef.current
    if (!light) return

    // mapSize / bias / normalBias are NOT set here: they are props on the
    // <directionalLight> below. This effect runs AFTER the first frame, and
    // three.js allocates the shadow map on that frame at the default 512²
    // and never reallocates it. Setting 1024 here afterwards left a 512²
    // texture sampled as if it were 1024², so the shadow was read from the
    // wrong place and disappeared (found 25/09 with the floor preview:
    // no shadow at all until the map was forced to reallocate).

    fitterRef.current = new ShadowCameraFitter(light, {
      ...DEFAULT_SHADOW_FIT,
      padding: shadows.fitPadding,
      groundZ: shadows.fitGroundZ,
    })
    return () => {
      fitterRef.current = null
    }
  }, [shadows])

  // Track the subject. Throttled internally — this is not per-frame work.
  useFrame((_state, delta) => {
    fitterRef.current?.update(vrm, delta * 1000)
  })

  // DEV handle for the shadow-frustum probe: compare the fitted frustum against
  // the skeleton AND its floor projection. Cheaper than guessing why a shadow
  // looks cut off.
  useEffect(() => {
    if (!import.meta.env.DEV) return
    ;(window as unknown as { __shadow?: unknown }).__shadow = () => ({
      light: lightRef.current,
      vrm,
    })
  }, [vrm])

  // ── Configure VRM meshes: castShadow / receiveShadow ──────────────────
  // Outline meshes (BackSide) must NOT cast shadows — they create doubled
  // shadow artifacts.
  useEffect(() => {
    if (!vrm?.scene) return

      vrm.scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.SkinnedMesh) {
          const mat = object.material as THREE.Material
          const isOutline =
            mat.side === THREE.BackSide ||
            (mat.name && mat.name.toLowerCase().includes('outline'))

            // Xóa outline pass
          if (isOutline) {
            object.visible = false
          } else {
            // TODO: Tạm tắt ghi đè material — giữ nguyên MToon để giữ màu gốc
            // object.material = new THREE.MeshStandardMaterial({
            //   color: 0xdddddd,
            //   roughness: 0.8,
            // })
            object.castShadow = true
            object.receiveShadow = true
          }
          
          // Disable frustum culling for skinned meshes. Root motion moves the
          // bones far from the origin, but the mesh bounding box/sphere is static
          // (computed in rest pose at origin). When the camera looks at the moved
          // character, the original origin might fall outside the frustum, causing
          // three.js to aggressively cull the hair/face meshes and make them disappear.
          object.frustumCulled = false
        }
      })
  }, [vrm])

  return (
    <>
      {/* ── Main Light: the ONLY NdotL contributor for MToon ────────── */}
      <directionalLight
        ref={lightRef}
        color={main.color}
        intensity={main.intensity}
        position={main.position}
        castShadow={main.castShadow}
        // Applied at creation, before the first frame allocates the map. See
        // the note in the fitter effect above.
        shadow-mapSize={[shadows.mapSize, shadows.mapSize]}
        shadow-bias={shadows.bias}
        shadow-normalBias={shadows.normalBias}
      />

      {/* ── Hemisphere: ambient fill, no directional influence ───────── */}
      <hemisphereLight
        color={ambient.skyColor}
        groundColor={ambient.groundColor}
        intensity={ambient.intensity}
      />

      {/* ── Ground: a visible textured floor if configured, otherwise the
          invisible plane that only catches the directional shadow. Never
          both — the shadow would be drawn twice. ─────────────────────── */}
      {FLOOR_TEXTURES ? (
        // Own boundary: the textures load after the avatar, never in front of it.
        <Suspense fallback={null}>
          <GroundFloor textures={FLOOR_TEXTURES} />
        </Suspense>
      ) : (
        <mesh
          position={[0, 0, 0]}
          receiveShadow
        >
          <planeGeometry args={[ground.planeSize, ground.planeSize]} />
          <shadowMaterial
            transparent
            opacity={ground.shadowMaterialOpacity}
          />
        </mesh>
      )}
    </>
  )
}
