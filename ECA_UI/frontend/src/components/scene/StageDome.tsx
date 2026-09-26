/**
 * StageDome — a procedural sky dome around the character ("stage" backdrop).
 *
 * The approach of character-viewer stages (e.g. HoYoverse-style "character
 * spheres"): the backdrop is a real 3D sphere at a fixed size around the
 * character, not an image at infinity. So it has scale and parallax, and the
 * character is always in proportion to it. Unlike a photo it has no furniture or
 * straight lines whose perspective could look wrong at any camera distance.
 *
 * Everything is procedural (no texture file): dark zenith → glowing horizon
 * band → darker floor below, soft nebula noise, and hashed stars. The floor
 * under the character is the dome's lower half; SceneLighting keeps only the
 * invisible shadow plane on top, so the shadow still lands (backdropOwnsFloor).
 *
 * Seen, never lit: a ShaderMaterial ignores lights, and it never touches
 * scene.environment (MToon is tuned for one directional light).
 *
 * One palette per UI theme (ENV_CONFIG…dome.dark / .light). A theme switch
 * eases the uniforms over ~0.5 s on the same material — no re-mount, no flash.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { ENV_CONFIG } from '../../config/environmentConfig'

interface Palette {
  zenith: string
  horizon: string
  floor: string
  glowStrength: number
  glowWidth: number
  skyFade: number
  nebula: number
  starDensity: number
  starBrightness: number
}

/** Rate of the theme crossfade, 1/s (≈95 % done in 0.5 s). */
const THEME_EASE = 6

const COLOR_KEYS = ['zenith', 'horizon', 'floor'] as const
const NUMBER_KEYS = ['glowStrength', 'glowWidth', 'skyFade', 'nebula', 'starDensity', 'starBrightness'] as const
const UNIFORM: Record<(typeof COLOR_KEYS)[number] | (typeof NUMBER_KEYS)[number], string> = {
  zenith: 'uZenith',
  horizon: 'uHorizon',
  floor: 'uFloor',
  glowStrength: 'uGlowStrength',
  glowWidth: 'uGlowWidth',
  skyFade: 'uSkyFade',
  nebula: 'uNebula',
  starDensity: 'uStarDensity',
  starBrightness: 'uStarBrightness',
}

const vertexShader = /* glsl */ `
  varying vec3 vDir;
  void main() {
    // Object space = direction from the dome centre (the mesh is never rotated).
    vDir = position;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`

const fragmentShader = /* glsl */ `
  uniform vec3 uZenith;
  uniform vec3 uHorizon;
  uniform vec3 uFloor;
  uniform float uGlowWidth;
  uniform float uSkyFade;
  uniform float uGlowStrength;
  uniform float uNebula;
  uniform float uStarDensity;
  uniform float uStarBrightness;
  varying vec3 vDir;

  // Dave Hoskins' hash — stable across GPUs, no texture lookups.
  float hash13(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.zyx + 31.32);
    return fract((p.x + p.y) * p.z);
  }

  float noise3(vec3 p) {
    vec3 i = floor(p);
    vec3 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(mix(hash13(i), hash13(i + vec3(1, 0, 0)), f.x),
          mix(hash13(i + vec3(0, 1, 0)), hash13(i + vec3(1, 1, 0)), f.x), f.y),
      mix(mix(hash13(i + vec3(0, 0, 1)), hash13(i + vec3(1, 0, 1)), f.x),
          mix(hash13(i + vec3(0, 1, 1)), hash13(i + vec3(1, 1, 1)), f.x), f.y),
      f.z);
  }

  float fbm(vec3 p) {
    float v = 0.0, a = 0.5;
    for (int i = 0; i < 4; i++) { v += a * noise3(p); p *= 2.03; a *= 0.5; }
    return v;
  }

  float stars(vec3 d, float scale) {
    vec3 p = d * scale;
    vec3 cell = floor(p);
    if (hash13(cell) < 1.0 - uStarDensity) return 0.0;
    vec3 jitter = vec3(hash13(cell + 1.7), hash13(cell + 3.1), hash13(cell + 5.3)) - 0.5;
    float dist = length(fract(p) - 0.5 - jitter * 0.6);
    return smoothstep(0.09, 0.0, dist) * (0.35 + 0.65 * hash13(cell + 9.1));
  }

  void main() {
    vec3 d = normalize(vDir);
    float h = d.z; // Z-up: 1 overhead, 0 at the horizon, -1 straight down

    // Sky darkens quickly above the horizon (uSkyFade), so the glow reads as a
    // band, not a lavender wash; the floor darkens within uGlowWidth below it.
    vec3 sky = mix(uHorizon, uZenith, pow(smoothstep(0.0, uSkyFade, h), 0.7));
    vec3 ground = mix(uHorizon, uFloor, smoothstep(0.0, uGlowWidth, -h));
    vec3 col = h >= 0.0 ? sky : ground;

    // The glowing band that sits behind the character's legs.
    float band = exp(-pow(h / uGlowWidth, 2.0) * 3.0);
    col += uHorizon * band * uGlowStrength;

    // Soft nebula clouds, strongest in the sky.
    float n = fbm(d * 2.5 + vec3(3.7, 1.3, 0.0));
    col *= 1.0 + uNebula * (n - 0.5) * 2.0 * (0.4 + 0.6 * smoothstep(-0.2, 0.4, h));

    // Two layers of stars; dimmer below the horizon, lost in the glow.
    float s = stars(d, 140.0) + 0.6 * stars(d + 0.37, 260.0);
    float starFade = mix(0.35, 1.0, smoothstep(-0.1, 0.35, h)) * (1.0 - band * 0.8);
    col += vec3(s * uStarBrightness * starFade);

    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }
`

export default function StageDome({ theme }: { theme: 'light' | 'dark' }) {
  const { dome } = ENV_CONFIG.environment.background
  const [hx, hy] = ENV_CONFIG.character.home

  // Built once with the palette the app starts in; later switches ease in.
  const [initialTheme] = useState(theme)
  const material = useMemo(() => {
    const start: Palette = dome[initialTheme]
    const uniforms: Record<string, THREE.IUniform> = {}
    for (const k of COLOR_KEYS) uniforms[UNIFORM[k]] = { value: new THREE.Color(start[k]) }
    for (const k of NUMBER_KEYS) uniforms[UNIFORM[k]] = { value: start[k] }
    return new THREE.ShaderMaterial({
      vertexShader,
      fragmentShader,
      uniforms,
      side: THREE.BackSide, // seen from inside
      depthWrite: false, // always behind everything
      fog: false,
    })
  }, [dome, initialTheme])

  // Target palette as THREE.Colors, rebuilt only when the theme changes.
  const target = useMemo(() => {
    const p: Palette = dome[theme]
    return {
      colors: COLOR_KEYS.map((k) => new THREE.Color(p[k])),
      numbers: NUMBER_KEYS.map((k) => p[k]),
    }
  }, [dome, theme])
  const settled = useRef(true)
  useEffect(() => {
    settled.current = false
  }, [target])

  useFrame((_, delta) => {
    if (settled.current) return
    const a = 1 - Math.exp(-THEME_EASE * delta)
    let remaining = 0
    COLOR_KEYS.forEach((k, i) => {
      const c = material.uniforms[UNIFORM[k]].value as THREE.Color
      c.lerp(target.colors[i], a)
      remaining = Math.max(remaining, Math.abs(c.r - target.colors[i].r), Math.abs(c.g - target.colors[i].g), Math.abs(c.b - target.colors[i].b))
    })
    NUMBER_KEYS.forEach((k, i) => {
      const u = material.uniforms[UNIFORM[k]]
      u.value += (target.numbers[i] - u.value) * a
      remaining = Math.max(remaining, Math.abs(target.numbers[i] - u.value))
    })
    if (remaining < 1e-3) {
      // Snap the last fraction so the palette is exact once settled.
      COLOR_KEYS.forEach((k, i) => (material.uniforms[UNIFORM[k]].value as THREE.Color).copy(target.colors[i]))
      NUMBER_KEYS.forEach((k, i) => (material.uniforms[UNIFORM[k]].value = target.numbers[i]))
      settled.current = true
    }
  })
  const geometry = useMemo(() => new THREE.SphereGeometry(dome.radius, 96, 48), [dome.radius])

  useEffect(() => () => {
    material.dispose()
    geometry.dispose()
  }, [material, geometry])

  return (
    <mesh
      geometry={geometry}
      material={material}
      // Centred on the character's home; the horizon sits at `horizonZ`.
      position={[hx, hy, dome.horizonZ]}
      renderOrder={-1}
      frustumCulled={false}
    />
  )
}
