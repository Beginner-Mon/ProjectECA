/**
 * Centralized environment configuration.
 *
 * Every numeric / color value that affects the 3D rendering environment lives
 * here. Scene components import from this file — nothing is hardcoded in JSX.
 * To tune the look: edit values here, hot-reload picks them up immediately.
 */

import * as THREE from 'three'

export const ENV_CONFIG = {
  // ── Character ─────────────────────────────────────────────────────────
  character: {
    /** Where the model group is authored, Z-up world. "Reset position" returns
     *  the character here, and the floor disc is centred under it. */
    home: [0, 1.5, 0] as [number, number, number],
  },

  // ── Debug ─────────────────────────────────────────────────────────────
  debug: {
    // Off by default, like showAxes: a debug overlay that users saw as lines
    // drawn on the floor. The Graphics settings toggle still turns it on.
    showGrid: false,
    // Off by default: the axis labels are DOM overlays and were shipping to
    // production users, who then had to find the Graphics toggle to hide them.
    // The setting persists per browser, so a developer turns it on once.
    showAxes: false,
  },

  // ── Renderer & Color Pipeline ─────────────────────────────────────────
  renderer: {
    toneMapping: THREE.NoToneMapping, // Chuẩn cho MToon Anime, bảo toàn màu gốc
    toneMappingExposure: 1.0,         // Trả về mặc định
    outputColorSpace: THREE.SRGBColorSpace,
  },

  // ── Lighting ──────────────────────────────────────────────────────────
  // RULE: only ONE directional light influences MToon NdotL toon-shading.
  // The hemisphere light is ambient-only (no directional influence).
  lighting: {
    main: {
      color: '#fffaf0',                       // warm white — avoids blue cast on skin
      intensity: 2.0,                         // Đèn chính chuẩn
      position: [0, 3, 8] as [number, number, number], // front, slightly above — flattering for anime faces
      castShadow: true, // true = harsh shadows on face; false = soft fill from ambient
    },
    ambient: {
      skyColor: '#b4c7e0',                    // cool sky fill
      groundColor: '#4a3728',                 // warm ground bounce
      intensity: 1,                         // Không làm phai mất bóng mờ
    },
  },

  // ── Shadows ───────────────────────────────────────────────────────────
  shadows: {
    // MUST NOT be PCFSoftShadowMap — that constant is deprecated and three.js
    // silently rewrites it: `WebGLShadowMap.render()` warns and does
    // `this.type = PCFShadowMap`. R3F then re-applies our value on every render
    // of <Canvas> and flags `shadowMap.needsUpdate = true` because the type
    // "changed" — so each React re-render forced a FULL shadow-map rebuild,
    // which is the black flash. Naming the value three.js actually uses breaks
    // that ping-pong; the rendered result is identical.
    type: THREE.PCFShadowMap as THREE.ShadowMapType,
    mapSize: 1024,
    bias: -0.001, // increased negative bias to prevent shadow acne on neck/hair
    normalBias: 0.02,
    // Frustum extents are AUTO-FITTED to the character every frame — see
    // lib/shadowFit.ts. The old fixed box (cameraSize 2.5 centred on the world
    // origin) left the subject 0.43 units from the edge while wasting ~92% of
    // the shadow map, which is how shadows got sliced off in a straight line.
    // Enlarging the box would only waste more map and blur the result.
    /** Metres of slack around the skeleton.
     *  NOT cosmetic: the fit is computed from ~50 JOINTS, but what casts the
     *  shadow is the MESH — skirt, hair, ribbons, wings reach well past any
     *  bone. A joint-only fit reports "nothing outside the frustum" while the
     *  silhouette is visibly sliced (that is exactly how the fixed-box version
     *  passed a bone-based check yet clipped on screen). */
    fitPadding: 0.35,
    /** World height of the shadow-receiving floor (ground plane sits at z=0). */
    fitGroundZ: 0,
  },

  // ── Ground ────────────────────────────────────────────────────────────
  ground: {
    y: -1.5,                                  // matches model group position.y
    // Real shadow-catching ground plane
    planeSize: 200,
    shadowMaterialOpacity: 0.35,
    // No contact shadow: removed 25/09, see SceneLighting.tsx header.

    /** Visible textured floor (components/scene/GroundFloor.tsx). */
    floor: {
      /** Folder under src/asset/floors/. null = no visible floor: back to the
       *  invisible shadow-catching plane above. */
      id: 'wood-ash' as string | null,
      /** Metres covered by one repeat of the texture. Poliigon wood sets are
       *  authored at roughly 2 m square. */
      metresPerTile: 2,
      /** Disc radius, metres. Motions carry the character away from home, so
       *  this is generous; the edge fades out. */
      radius: 12,
      /** Fraction of the radius that stays fully opaque before the fade. */
      fadeStart: 0.45,
      /** Multiplies the colour map. The scene has no tone mapping and a 2.0
       *  directional light, so pure white would glare. Lower = darker floor. */
      tint: '#c8c8c8',
      normalStrength: 1,
      aoIntensity: 1,
      /** Floor centre, world XY: under the character's home. */
      center: [0, 1.5] as [number, number],
    },
  },

  // ── Environment / Background ──────────────────────────────────────────
  environment: {
    useGradient: true,                        // true = shader gradient; false = HDRI
    gradient: {
      // Unified colors — same lighting for both themes; only bg changes.
      dark:  { top: '#1a1a2e', bottom: '#2a2040' },
      light: { top: '#f0f2f8', bottom: '#e0e2ec' },
    },
    hdri: {
      preset: 'studio' as const,
      intensity: 0.3,                         // low for MToon — avoids over-reflection
    },
    iblResolution: 64,                        // low to avoid GPU memory issues (D3D11)
    /** Backdrop image (components/scene/SceneBackdrop.tsx). Same in every UI
     *  theme; takes priority over the gradient/HDRI above and hides the stars. */
    background: {
      /** Folder under src/asset/backgrounds/. null = the gradient/HDRI. */
      id: 'house' as string | null,
      /** 'dome' = procedural stage sphere around the character (StageDome),
       *  no image, `id` unused. 'flat' = ordinary picture, fixed, cover-fit.
       *  'panorama' = 2:1 equirectangular 360°, turns with the camera. */
      kind: 'dome' as 'dome' | 'flat' | 'panorama',
      /** Stage dome (kind 'dome'). Colours are sRGB hex. */
      dome: {
        /** Metres. Larger than the camera's max orbit (20) so it never leaves. */
        radius: 30,
        /** Height of the dome centre = where the horizon glow sits. 0 = floor
         *  level, so the band glows behind the character's legs. */
        horizonZ: 0,
        zenith: '#101233',
        /** Colour where sky meets floor. Keep it near zenith/floor for a smooth
         *  night sky; a bright colour (e.g. '#9c94e0') makes a glowing stripe
         *  across the middle of the screen — removed at the Owner's request. */
        horizon: '#221f52',
        floor: '#16133c',
        /** Extra glow added along the horizon. 0 = none (no bright band). */
        glowStrength: 0,
        /** Glow band half-width around the horizon (0..1 of the dome height). */
        glowWidth: 0.12,
        /** How high above the horizon the sky reaches the zenith colour. */
        skyFade: 0.35,
        /** Nebula cloud strength, 0 = none. */
        nebula: 0.35,
        /** Fraction of star cells that hold a star, 0..1. */
        starDensity: 0.12,
        starBrightness: 1.6,
      },
      /** Brightness multiplier. Below 1 dims the backdrop so the avatar reads first. */
      intensity: 1,
      /** Panorama only (three cannot blur a flat backdrop). 0 = sharp. */
      blurriness: 0,
      /**
       * Panorama only. Project the photo onto a real floor + dome around the
       * character (three's GroundedSkybox) instead of drawing it at infinity.
       * At infinity a panorama has no size: its scale is set by the camera's
       * 45° lens alone, so the room looked magnified around a 1.6 m avatar.
       * Grounded, the room gets real-world scale and parallax. null = infinite.
       */
      ground: {
        /** Height the photo was taken from, metres. Sets the room's scale:
         *  too small = room looks huge, too large = room looks like a dollhouse. */
        height: 1.6,
        /** Dome radius, metres: roughly the room's reach. Past it, walls are
         *  projected onto the dome; beyond it the infinite backdrop shows. */
        radius: 10,
        /** Turn the room around the character, degrees. */
        yawDeg: 0,
      } as { height: number; radius: number; yawDeg: number } | null,
    },
    showStars: {
      dark: true,
      light: false,
    },
  },

  // ── Post Processing ───────────────────────────────────────────────────
  // Subtle enhancements only — no cinematic look. Goal: depth, not drama.
  postProcessing: {
    enabled: true,
    bloom: {
      // OFF — Bloom is what caused the "màn hình chớp đen" report. Measured
      // with a CDP screencast at ~59fps over 19.5s, counting frames whose mean
      // luma fell below 75% of the median (a real black frame reads 17.8 vs a
      // normal 220):
      //
      //   everything on ................ 4 black frames
      //   SSAO off ..................... 3   (not SSAO)
      //   ContactShadows off ........... 5   (not ContactShadows)
      //   whole EffectComposer off ..... 0
      //   Bloom off, rest on ........... 0   ← isolated
      //   Bloom on with mipmapBlur ..... 6   (alternate blur path doesn't help)
      //
      // One isolated frame goes fully black roughly every 3-5s, unrelated to
      // any app event. At intensity 0.15 / threshold 0.85 the effect was barely
      // perceptible, so the trade is not close. Flip back to true only if the
      // underlying @react-three/postprocessing issue is fixed — and re-run the
      // screencast check before trusting it.
      enabled: false,
      intensity: 0.15,
      luminanceThreshold: 0.85,
      luminanceSmoothing: 0.4,
    },
    ssao: {
      enabled: true, // Bình thường là true
      intensity: 0.3,
      radius: 0.05,
      samples: 16,
    },
    vignette: {
      offset: 0.35,
      darkness: 0.1,
    },
  },

  // ── MToon Material Overrides ──────────────────────────────────────────
  // Only applies if `enabled: true`. Tweak shading at the material level
  // BEFORE the shader runs — cleaner than post-processing.
  /** "Softer shadows" toggle (Graphics settings; key `mtoon` kept so saved
   *  settings still apply). NOT a switch for toon shading — the avatar is always
   *  MToon. See lib/shadowOverride.ts. */
  mtoon: {
    enabled: false,
    shadingShiftFactor: 0.85, // 0-1: higher = smaller shadow areas on the face/body
    /** Shadow tint override. null = keep each model's own authored tint (the
     *  near-black '#1a1020' made skin shadows muddy on every character). */
    shadeColorHex: null as string | null,
  },

  // ── Floating Particles ────────────────────────────────────────────────
  particles: {
    enabled: true,                            // default ON
    count: 150,
    size: 0.02,
    color: '#a78bfa',
    opacity: 0.4,
  },
} as const
