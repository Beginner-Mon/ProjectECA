/**
 * SceneBackdrop — an image behind the scene, independent of the UI theme.
 *
 * Never touches `scene.environment`: the backdrop is seen, never lights the
 * avatar — MToon toon shading is tuned for exactly one directional light.
 *
 * Kinds (ENV_CONFIG.environment.background.kind):
 *  - 'flat'      an ordinary picture as `scene.background`. Fills the screen
 *                "cover"-style at any window shape; fixed while orbiting.
 *  - 'panorama'  a 2:1 equirectangular 360° image. Two ways to show it:
 *      · `ground` set (default): projected onto a real floor + dome around the
 *        character with three's GroundedSkybox, so the room has real-world
 *        scale and parallax. Drawn at infinity instead, a panorama's size is
 *        set by the camera lens alone — at our 45° it looked magnified, the
 *        avatar a tenth of the room (Owner, 25/09).
 *      · `ground: null`: `scene.background` at infinity, optional blur.
 *    Either way the infinite version is also installed as `scene.background`,
 *    so a camera zoomed out past the dome still sees the room, not a void.
 *
 * Z-up: panoramas and GroundedSkybox are authored Y-up; both are rotated here.
 */

import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import { useTexture } from '@react-three/drei'
import { useThree } from '@react-three/fiber'
import { GroundedSkybox } from 'three/examples/jsm/objects/GroundedSkybox.js'
import { ENV_CONFIG } from '../../config/environmentConfig'
import { coverTransform } from '../../lib/backgroundAssets'

/* eslint-disable react-hooks/immutability -- scene.background and textures are
   three.js objects, configured imperatively by design (same as RendererSetup). */
export default function SceneBackdrop({ url }: { url: string }) {
  const { kind, intensity, blurriness, ground } = ENV_CONFIG.environment.background
  const texture = useTexture(url)
  const scene = useThree((s) => s.scene)
  const size = useThree((s) => s.size)

  const imageAspect = useMemo(() => {
    const img = texture.image as { width?: number; height?: number } | undefined
    return img?.width && img?.height ? img.width / img.height : 16 / 9
  }, [texture])

  // ── The infinite backdrop (all kinds) ──────────────────────────────────
  // A clone for panoramas: the grounded mesh samples the SAME image with plain
  // UV mapping, while scene.background needs equirectangular mapping. They
  // share the image data, only the sampling settings differ.
  const backgroundTexture = useMemo(() => {
    const t = kind === 'panorama' ? texture.clone() : texture
    t.colorSpace = THREE.SRGBColorSpace
    if (kind === 'panorama') t.mapping = THREE.EquirectangularReflectionMapping
    t.needsUpdate = true
    return t
  }, [texture, kind])

  useEffect(() => {
    if (kind === 'panorama') {
      scene.backgroundRotation.set(Math.PI / 2, 0, 0) // Y-up image, Z-up world
      scene.backgroundBlurriness = blurriness
    }
    scene.background = backgroundTexture
    scene.backgroundIntensity = intensity
    return () => {
      if (scene.background === backgroundTexture) scene.background = null
      scene.backgroundIntensity = 1
      scene.backgroundBlurriness = 0
      scene.backgroundRotation.set(0, 0, 0)
      if (backgroundTexture !== texture) backgroundTexture.dispose()
    }
  }, [scene, backgroundTexture, texture, kind, intensity, blurriness])

  // Flat: re-crop on every resize so the image covers the view undistorted.
  useEffect(() => {
    if (kind !== 'flat') return
    const { repeat, offset } = coverTransform(size.width / size.height, imageAspect)
    texture.repeat.set(repeat[0], repeat[1])
    texture.offset.set(offset[0], offset[1])
    texture.updateMatrix()
  }, [texture, kind, size.width, size.height, imageAspect])

  // ── The grounded panorama ──────────────────────────────────────────────
  const skybox = useMemo(() => {
    if (kind !== 'panorama' || !ground) return null
    texture.colorSpace = THREE.SRGBColorSpace
    texture.needsUpdate = true
    const mesh = new GroundedSkybox(texture, ground.height, ground.radius)
    ;(mesh.material as THREE.MeshBasicMaterial).color.setScalar(intensity)
    // Its floor sits `height` below its centre (the photo's viewpoint). Y-up
    // → Z-up, and lift it so that floor lands 1 cm under z = 0: under the
    // character's feet, below our floor / shadow plane, never z-fighting them.
    mesh.rotation.x = Math.PI / 2
    mesh.position.z = ground.height - 0.01
    return mesh
  }, [texture, kind, ground, intensity])

  useEffect(() => () => {
    if (!skybox) return
    skybox.geometry.dispose()
    ;(skybox.material as THREE.Material).dispose()
  }, [skybox])

  if (!skybox || !ground) return null
  const [hx, hy] = ENV_CONFIG.character.home
  return (
    // Centred on the character's home, so she stands where the photo was taken.
    <group position={[hx, hy, 0]} rotation={[0, 0, THREE.MathUtils.degToRad(ground.yawDeg)]}>
      <primitive object={skybox} />
    </group>
  )
}
/* eslint-enable react-hooks/immutability */
