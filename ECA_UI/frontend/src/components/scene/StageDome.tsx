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
 */

import { useEffect, useMemo } from 'react'
import * as THREE from 'three'
import { ENV_CONFIG } from '../../config/environmentConfig'

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

export default function StageDome() {
  const { dome } = ENV_CONFIG.environment.background
  const [hx, hy] = ENV_CONFIG.character.home

  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader,
        fragmentShader,
        uniforms: {
          uZenith: { value: new THREE.Color(dome.zenith) },
          uHorizon: { value: new THREE.Color(dome.horizon) },
          uFloor: { value: new THREE.Color(dome.floor) },
          uGlowWidth: { value: dome.glowWidth },
          uSkyFade: { value: dome.skyFade },
          uGlowStrength: { value: dome.glowStrength },
          uNebula: { value: dome.nebula },
          uStarDensity: { value: dome.starDensity },
          uStarBrightness: { value: dome.starBrightness },
        },
        side: THREE.BackSide, // seen from inside
        depthWrite: false, // always behind everything
        fog: false,
      }),
    [dome],
  )
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
