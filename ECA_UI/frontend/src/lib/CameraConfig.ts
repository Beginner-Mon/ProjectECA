export interface CameraConfig {
  /** OrbitControls: allow mouse drag to pan camera up/down/left/right */
  enablePan: boolean
  /** OrbitControls: allow scroll wheel zoom */
  enableZoom: boolean
  /** Min orbit radius (zoom in limit) */
  minDistance: number
  /** Max orbit radius (zoom out limit) */
  maxDistance: number
  /** Auto-follow the VRM bone target (lookAt). Permanently off by design —
   *  the per-frame follow re-anchors the orbit target to the bone and wipes
   *  out any manual pan, so pan can never work while this is on. The camera
   *  still re-frames once on FSM camera-mode changes (see CharacterViewer). */
  followTarget: boolean
  /** Offset X (left/right) from the default camera position */
  offsetX: number
  /** Offset Y (up/down) from the default camera position */
  offsetY: number
  /** Offset Z (forward/backward) from the default camera position */
  offsetZ: number
  /** Lock per-axis: when true, right-drag/pan does not change that coordinate. */
  lockX: boolean
  lockY: boolean
  lockZ: boolean
}

export const DEFAULT_CAMERA_CONFIG: CameraConfig = {
  // Always on by design — the panel no longer offers a toggle.
  enablePan: true,
  // Always off by design — lookAt follow fights manual pan (see field doc).
  followTarget: false,
  enableZoom: true,
  // 0.5 m (was 1 m, 30-09): the head view sat exactly on the old 1 m floor,
  // so zooming in from it did nothing at all. The head preset now states its
  // 1 m itself (CharacterViewer CAMERA_RESPONSIVE_PRESETS) instead of
  // borrowing it from this clamp, so the default framing is unchanged.
  minDistance: 0.5,
  // 8 m (was 20, 03-10): zooming far out in the free/panning camera broke
  // features (Owner). Every automatic framing stays under it — the widest,
  // `hips` on a phone, is ~5 m — so this only limits the user's own zoom-out.
  // A per-mode limit would snap the camera in the moment a zoom-out switched
  // head/hips to manual.
  maxDistance: 8,
  offsetX: 0,
  offsetY: 0,
  offsetZ: 0,
  lockX: false,
  lockY: false,
  lockZ: false,
}

export interface CameraResponsivePreset {
  wideFraming: [number, number, number]
  narrowFraming: [number, number, number]
  /** Target Z shift on mobile (negative = target lower = model appears higher in frame).
   *  Lerped from 0 at desktop to this value at mobile. */
  narrowTargetZ: number
}

/** Aspect ratio thresholds for responsive camera offset interpolation.
 *  aspect ≥ wide → t=0 (full wide framing, desktop)
 *  aspect ≤ narrow → t=1 (full narrow framing, mobile portrait)
 *  Between → lerp */
export const ASPECT_RANGE = {
  narrow: 0.6,
  wide: 1.5,
} as const
