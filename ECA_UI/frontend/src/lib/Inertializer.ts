import * as THREE from 'three'
import type { VRM } from '@pixiv/three-vrm'
import { VRMHumanBoneName } from '@pixiv/three-vrm'

// ── Polynomial — Bollo quintic (GDC 2018) ──────────────────────────────

export interface InertialCoeffs {
  A: number
  B: number
  C: number
  a0: number
  v0: number
  x0: number
  T: number
}

/**
 * Compute inertial blending coefficients for a single scalar channel.
 * x0 ≥ 0 is the initial offset magnitude, v0 the initial velocity along that
 * offset, T the desired duration (seconds). Handles clamping as per spec:
 *   if v0 > 0 → v0 = 0  (offset growing → suppress)
 *   if v0 < 0 → T = min(T, -5*x0/v0) (prevent overshoot)
 *
 * Returns coeffs for
 *   x(t) = A t⁵ + B t⁴ + C t³ + (a0/2) t² + v0 t + x0
 * with boundary conditions x(T)=x'(T)=x''(T)=0.
 */
export function computeInertialCoeffs(x0: number, v0: number, T: number): InertialCoeffs {
  // Trivial: no offset → no motion
  if (Math.abs(x0) < 1e-9) {
    return { A: 0, B: 0, C: 0, a0: 0, v0: 0, x0: 0, T }
  }

  // Clamp expanding velocity
  if (v0 > 0) v0 = 0

  // Clamp duration to prevent overshoot when moving toward target fast
  if (v0 < 0) {
    const tMax = (-5 * x0) / v0 // v0 negative → tMax positive
    if (tMax < T) T = tMax
  }

  // Guard against degenerate T after clamping
  if (T < 1e-6) T = 1e-6

  const T2 = T * T
  const T3 = T2 * T
  const T4 = T3 * T
  const T5 = T4 * T

  const a0 = (-8 * v0 * T - 20 * x0) / T2
  const A = -(a0 * T2 + 6 * v0 * T + 12 * x0) / (2 * T5)
  const B = (3 * a0 * T2 + 16 * v0 * T + 30 * x0) / (2 * T4)
  const C = -(3 * a0 * T2 + 12 * v0 * T + 20 * x0) / (2 * T3)

  return { A, B, C, a0, v0, x0, T }
}

export function evalInertialX(t: number, c: InertialCoeffs): number {
  if (c.T <= 0) return 0
  if (t <= 0) return c.x0
  if (t >= c.T) return 0
  const { A, B, C, a0, v0, x0 } = c
  const t2 = t * t
  const t3 = t2 * t
  const t4 = t3 * t
  const t5 = t4 * t
  return A * t5 + B * t4 + C * t3 + (a0 / 2) * t2 + v0 * t + x0
}

export function evalInertialV(t: number, c: InertialCoeffs): number {
  if (c.T <= 0) return 0
  if (t <= 0) return c.v0
  if (t >= c.T) return 0
  const { A, B, C, a0, v0 } = c
  // x'(t) = 5A t⁴ + 4B t³ + 3C t² + a0 t + v0
  return 5 * A * Math.pow(t, 4) + 4 * B * Math.pow(t, 3) + 3 * C * t * t + a0 * t + v0
}

export function evalInertialA(t: number, c: InertialCoeffs): number {
  if (c.T <= 0) return 0
  if (t <= 0) return c.a0
  if (t >= c.T) return 0
  const { A, B, C, a0 } = c
  // x''(t) = 20A t³ + 12B t² + 6C t + a0
  return 20 * A * Math.pow(t, 3) + 12 * B * t * t + 6 * C * t + a0
}

// ── Pose Inertializer ─────────────────────────────────────────────────

/** Angle at which a transition earns the full `blendSec`. */
export const FULL_ANGLE_RAD = (45 * Math.PI) / 180
/** Floor on the blend duration, as a fraction of `blendSec`. */
export const MIN_FRACTION = 0.6

/**
 * How long the blend runs, given how far the pose has to travel.
 *
 * The mapping is deliberately FLAT. The first version scaled duration linearly
 * with the angle, all the way down to 0.35 of the ceiling — which made every
 * small transition (`greeting → idle` and friends) finish in a third of the
 * time the crossfade it replaced took. Measured against the old 0.5 s fades it
 * read as twitchy, not human, and the quintic makes it worse by front-loading
 * most of the travel into the first ~40% of the window.
 *
 * People do not move proportionally faster over shorter distances. By Fitts'
 * law movement time grows with the LOGARITHM of amplitude, so across the range
 * of angles a pose transition actually spans it is nearly constant. This curve
 * keeps the duration between MIN_FRACTION and 1 of `blendSec` and saturates at
 * FULL_ANGLE_RAD — a much flatter response than the linear ramp.
 */
export function adaptiveDuration(thetaRad: number, maxSec: number): number {
  const ratio = Math.max(0, thetaRad) / FULL_ANGLE_RAD
  const curve = Math.min(1, Math.log2(1 + ratio)) // 0 at θ=0, 1 at θ=FULL_ANGLE
  return maxSec * (MIN_FRACTION + (1 - MIN_FRACTION) * curve)
}

/**
 * Kinetic-chain stagger: how much later each step away from the hips starts
 * moving. A person does not start every joint on the same frame — the hips and
 * spine lead and the limbs trail by roughly 20-50 ms per segment. Applying the
 * same offset curve to every bone simultaneously is what makes an otherwise
 * correct blend read as a rigid morph.
 */
const STAGGER_PER_LEVEL_SEC = 0.025
/** Total stagger never eats more than this share of the blend. */
const STAGGER_MAX_FRACTION = 0.35

let debugEnabled = false
/** Dev-only: log θ, duration and stagger for every transition. */
export function setInertialDebug(enabled: boolean): void {
  debugEnabled = enabled
}
export function isInertialDebug(): boolean {
  return debugEnabled
}

/** Bones excluded from theta calculation (head/neck driven by HeadController) */
const EXCLUDED_BONES = new Set<string>([
  VRMHumanBoneName.Head,
  VRMHumanBoneName.Neck,
])

interface BoneOffset {
  axis: THREE.Vector3
  coeffs: InertialCoeffs
}

export class PoseInertializer {
  private readonly bones: THREE.Object3D[]
  private readonly boneNames: string[]
  private readonly hipsNode: THREE.Object3D | null

  // History buffers for velocity estimation (ping-pong)
  private prevQuats: THREE.Quaternion[] = []
  private currQuats: THREE.Quaternion[] = []
  private prevHipsPos = new THREE.Vector3()
  private currHipsPos = new THREE.Vector3()
  private historyValid = false
  private historyCount = 0

  // Raw dst buffer to handle bones without tracks (fix drift)
  private rawDstQuats: THREE.Quaternion[] = []
  private rawDstHipsPos = new THREE.Vector3()
  private rawDstValid = false

  // Active inertialization state
  private offsets: BoneOffset[] = []
  private hipsOffset: { dir: THREE.Vector3; coeffs: InertialCoeffs } | null = null
  private elapsed = 0
  private duration = 0
  private active = false

  /** Hops from the hips to each bone; drives the kinetic-chain stagger. */
  private readonly boneDepths: number[]
  private readonly maxDepth: number
  /** Seconds each bone waits before its offset starts decaying. */
  private delays: number[] = []

  // Group handling for root motion (fix pop) — complementary 1 - x/x0
  private groupTarget: THREE.Object3D | null = null
  private groupBase = new THREE.Vector3()
  private groupAccum = new THREE.Vector3()
  private groupPending = new THREE.Vector3()
  private groupCoeffs: InertialCoeffs | null = null
  private groupX0 = 0
  private groupActive = false

  // Scratch (avoid per-frame alloc)
  private readonly scratchQuat = new THREE.Quaternion()
  private readonly scratchQuat2 = new THREE.Quaternion()
  private readonly scratchQuat3 = new THREE.Quaternion()
  private readonly scratchVec = new THREE.Vector3()
  private readonly scratchVec2 = new THREE.Vector3()

  constructor(vrm: VRM) {
    const humanoid = vrm.humanoid
    const bones: THREE.Object3D[] = []
    const names: string[] = []
    if (humanoid) {
      for (const name of Object.keys(humanoid.humanBones)) {
        const node = humanoid.getNormalizedBoneNode(name as never)
        if (node) {
          bones.push(node)
          names.push(name)
        }
      }
    }
    this.bones = bones
    this.boneNames = names
    this.hipsNode = humanoid?.getNormalizedBoneNode(VRMHumanBoneName.Hips) ?? null

    // Depth is measured on the real rig rather than a hard-coded bone table, so
    // it stays right for any skeleton the retargeters can hit. A bone that is
    // not under the hips (or when there are no hips at all) gets depth 0, which
    // simply means "no stagger" rather than a wrong one.
    this.boneDepths = bones.map((b) => hopsToAncestor(b, this.hipsNode))
    this.maxDepth = this.boneDepths.reduce((a, b) => Math.max(a, b), 0)
    this.delays = bones.map(() => 0)

    // Initialize history buffers
    this.prevQuats = bones.map(() => new THREE.Quaternion())
    this.currQuats = bones.map(() => new THREE.Quaternion())
    this.rawDstQuats = bones.map(() => new THREE.Quaternion())
    // Capture initial pose
    this.captureCurrentToCurr()
    this.historyValid = false
  }

  get isActive(): boolean {
    return this.active
  }

  /** Active blend duration (for testing/adaptive verification). */
  get activeDuration(): number {
    return this.duration
  }

  /** Hips coeffs for root-motion sync (read-only). */
  get hipsCoeffs(): InertialCoeffs | null {
    return this.hipsOffset?.coeffs ?? null
  }

  get groupPendingOffset(): THREE.Vector3 | null {
    return this.groupActive ? this.groupPending.clone() : null
  }

  /**
   * Provide group target for root-motion handling.
   * Call when group is created (CharacterViewer). Idempotent.
   */
  setGroupTarget(target: THREE.Object3D): void {
    if (this.groupTarget === target) return
    this.groupTarget = target
    // Do not reset accum — keep world offset across model switches if any.
    // Base will be recomputed in begin() as target.position - accum.
  }

  /**
   * Call every frame, after mixer + inertializer updates, to keep 1-frame history.
   * Must be called even when not active, so velocity at transition time is fresh.
   */
  recordFrame(): void {
    // Shift curr → prev
    for (let i = 0; i < this.bones.length; i++) {
      this.prevQuats[i].copy(this.currQuats[i])
    }
    this.prevHipsPos.copy(this.currHipsPos)
    this.captureCurrentToCurr()
    this.historyCount++
    if (this.historyCount >= 2) this.historyValid = true
  }

  private captureCurrentToCurr(): void {
    for (let i = 0; i < this.bones.length; i++) {
      this.currQuats[i].copy(this.bones[i].quaternion)
    }
    if (this.hipsNode) {
      this.currHipsPos.copy(this.hipsNode.position)
    } else {
      this.currHipsPos.set(0, 0, 0)
    }
  }

  /**
   * Restore raw dst before mixer so bones without tracks don't drift.
   * Must be called BEFORE mixer.update(delta) when inertial is active.
   */
  restoreRaw(): void {
    if (!this.active || !this.rawDstValid) return
    for (let i = 0; i < this.bones.length; i++) {
      this.bones[i].quaternion.copy(this.rawDstQuats[i])
    }
    if (this.hipsNode) {
      this.hipsNode.position.copy(this.rawDstHipsPos)
    }
    // Group is handled separately in update via complementary curve
  }

  /**
   * Begin inertialization.
   * Must be called AFTER new clip has been played and mixer.update(0) flushed,
   * so bones already hold P_dst(0). History buffers hold P_src and P_prev.
   *
   * @param maxSec  ceiling duration from AnimationStates (blendSec)
   * @param dt      frame delta at transition time (for velocity)
   * @param handoffRootMotion
   *   Whether the state being left carried real root translation, so its hips
   *   displacement should be handed to the model group and KEPT. Off by
   *   default: every clip ends with the hips a few millimetres away from the
   *   next clip's rest, and folding that into `groupAccum` on every transition
   *   walks the character off its mark over a session. Only clips that
   *   genuinely travel get the hand-off — the same gate `CharacterViewer`
   *   already applies to `RootMotionAccumulator.beginOneShot`.
   */
  begin(maxSec: number, dt: number, handoffRootMotion = false): void {
    if (this.bones.length === 0) return
    const safeDt = dt > 1e-6 ? dt : 1 / 60

    const srcQuats = this.currQuats
    // dst is current bone quats AFTER mixer.update(0)
    const dstQuats: THREE.Quaternion[] = []
    for (let i = 0; i < this.bones.length; i++) {
      dstQuats.push(this.scratchQuat3.copy(this.bones[i].quaternion).clone())
    }
    const srcHipsPos = this.currHipsPos.clone()
    const dstHipsPos = this.hipsNode ? this.hipsNode.position.clone() : new THREE.Vector3()

    // Capture raw dst for restoreRaw on next frames
    for (let i = 0; i < this.bones.length; i++) {
      this.rawDstQuats[i].copy(dstQuats[i])
    }
    this.rawDstHipsPos.copy(dstHipsPos)
    this.rawDstValid = true

    // Also capture group base if needed
    if (this.groupTarget) {
      this.groupBase.copy(this.groupTarget.position).sub(this.groupAccum)
    }

    // Compute per-bone offsets and find theta (max angle among non-excluded)
    let theta = 0
    const tempOffsets: Array<{ axis: THREE.Vector3; x0: number; v0: number }> = []

    for (let i = 0; i < this.bones.length; i++) {
      const src = srcQuats[i]
      const dst = dstQuats[i]
      const name = this.boneNames[i]

      // q_off = src * dst^-1
      this.scratchQuat.copy(dst).invert()
      this.scratchQuat2.copy(src).multiply(this.scratchQuat)

      const qOff = this.scratchQuat2
      // Normalize and ensure shortest arc (w >=0)
      if (qOff.w < 0) {
        qOff.x = -qOff.x
        qOff.y = -qOff.y
        qOff.z = -qOff.z
        qOff.w = -qOff.w
      }
      qOff.normalize()

      const wClamped = Math.max(-1, Math.min(1, qOff.w))
      const angle = 2 * Math.acos(wClamped) // [0, π]
      if (angle < 1e-6) {
        tempOffsets.push({ axis: new THREE.Vector3(1, 0, 0), x0: 0, v0: 0 })
        continue
      }
      const s = Math.sin(angle / 2)
      const axis = new THREE.Vector3()
      if (Math.abs(s) < 1e-6) {
        axis.set(1, 0, 0)
      } else {
        axis.set(qOff.x / s, qOff.y / s, qOff.z / s).normalize()
      }

      // Velocity along offset axis: delta = src * prev^-1
      let v0 = 0
      if (this.historyValid) {
        const prev = this.prevQuats[i]
        this.scratchQuat.copy(prev).invert()
        this.scratchQuat2.copy(src).multiply(this.scratchQuat)
        const qDelta = this.scratchQuat2
        if (qDelta.w < 0) {
          qDelta.x = -qDelta.x
          qDelta.y = -qDelta.y
          qDelta.z = -qDelta.z
          qDelta.w = -qDelta.w
        }
        qDelta.normalize()
        const wD = Math.max(-1, Math.min(1, qDelta.w))
        const deltaAngle = 2 * Math.acos(wD)
        if (deltaAngle > 1e-6) {
          const sD = Math.sin(deltaAngle / 2)
          // Reuse scratchVec as deltaAxis to avoid alloc
          const deltaAxis = this.scratchVec
          if (Math.abs(sD) < 1e-6) deltaAxis.set(axis.x, axis.y, axis.z)
          else deltaAxis.set(qDelta.x / sD, qDelta.y / sD, qDelta.z / sD).normalize()
          const dot = deltaAxis.dot(axis)
          v0 = (deltaAngle / safeDt) * dot
        }
      }

      tempOffsets.push({ axis, x0: angle, v0 })

      if (!EXCLUDED_BONES.has(name)) {
        if (angle > theta) theta = angle
      }
    }

    // Adaptive duration — also consider hips displacement
    const hipsMagForTheta = this.hipsNode
      ? this.scratchVec.copy(srcHipsPos).sub(dstHipsPos).length()
      : 0
    const hasHipsOffset = hipsMagForTheta > 1e-6
    let T: number
    if (theta > 1e-6) {
      T = adaptiveDuration(theta, maxSec)
    } else if (hasHipsOffset) {
      // Bones have no offset but hips do → use full duration for hips
      T = maxSec
    } else {
      // No significant offset → no-op
      this.active = false
      this.offsets = []
      this.hipsOffset = null
      this.groupActive = false
      this.groupCoeffs = null
      return
    }

    // Kinetic-chain stagger. Bounded both absolutely and as a share of T, so a
    // short blend does not end up mostly waiting.
    const perLevel =
      this.maxDepth > 0
        ? Math.min(STAGGER_PER_LEVEL_SEC, (T * STAGGER_MAX_FRACTION) / this.maxDepth)
        : 0
    for (let i = 0; i < this.delays.length; i++) {
      this.delays[i] = this.boneDepths[i] * perLevel
    }

    // Build per-bone coeffs with adaptive T
    const offsets: BoneOffset[] = []
    let maxT = 0
    for (let i = 0; i < tempOffsets.length; i++) {
      const { axis, x0, v0 } = tempOffsets[i]
      if (x0 < 1e-6) {
        offsets.push({ axis: axis.clone(), coeffs: computeInertialCoeffs(0, 0, T) })
        continue
      }
      const c = computeInertialCoeffs(x0, v0, T)
      offsets.push({ axis: axis.clone(), coeffs: c })
      // A staggered bone finishes that much later, so the blend has to outlive
      // it — otherwise the deepest bones get cut off mid-decay and snap.
      const finishesAt = this.delays[i] + c.T
      if (finishesAt > maxT) maxT = finishesAt
    }

    // Hips position offset
    let hipsOffset: { dir: THREE.Vector3; coeffs: InertialCoeffs } | null = null
    if (this.hipsNode) {
      this.scratchVec.copy(srcHipsPos).sub(dstHipsPos)
      const mag = this.scratchVec.length()
      if (mag > 1e-6) {
        const dir = this.scratchVec.clone().normalize()
        // Velocity along dir
        let vHips = 0
        if (this.historyValid) {
          this.scratchVec2.copy(srcHipsPos).sub(this.prevHipsPos)
          this.scratchVec2.multiplyScalar(1 / safeDt)
          vHips = this.scratchVec2.dot(dir)
        }
        const c = computeInertialCoeffs(mag, vHips, T)
        hipsOffset = { dir, coeffs: c }
        if (c.T > maxT) maxT = c.T
      }
    }

    // If all offsets trivial, no-op (but keep rawDst valid for restore)
    const hasMeaningful = offsets.some((o) => o.coeffs.x0 > 1e-6) || (hipsOffset && hipsOffset.coeffs.x0 > 1e-6)
    if (!hasMeaningful) {
      this.active = false
      this.offsets = []
      this.hipsOffset = null
      this.groupActive = false
      this.groupCoeffs = null
      return
    }

    this.offsets = offsets
    this.hipsOffset = hipsOffset
    this.elapsed = 0
    this.duration = maxT
    this.active = true

    // Group handling — complementary 1 - x/x0.
    //
    // The group's position is WORLD space (Z-up); `hipsNode.position` is
    // armature LOCAL space (Y-up). CharacterViewer renders the group with
    // `rotation={[π/2,0,0]}`, so local (x,y,z) lands on world (x,-z,y): copying
    // the local components straight across would drop the forward/back
    // translation entirely and turn vertical motion into sideways drift.
    //
    // Measure in world space rather than converting by hand — that also stays
    // correct for any transform sitting between the group and the bone, which
    // the group's own quaternion would miss. The bones hold dst right now, so:
    // read dst's world position, swap src in, read again, put dst back.
    if (handoffRootMotion && this.groupTarget && hipsOffset && this.hipsNode) {
      this.hipsNode.getWorldPosition(this.scratchVec) // dst, world
      this.scratchVec2.copy(this.hipsNode.position) // dst, local
      this.hipsNode.position.copy(srcHipsPos)
      this.hipsNode.getWorldPosition(this.groupPending) // src, world
      this.hipsNode.position.copy(this.scratchVec2)
      this.hipsNode.updateWorldMatrix(true, false)
      this.groupPending.sub(this.scratchVec)
      this.groupPending.z = 0 // GroundClamp owns world Z
      // If pending is near zero on XY, no group motion needed
      if (this.groupPending.lengthSq() < 1e-9) {
        this.groupActive = false
        this.groupCoeffs = null
        this.groupX0 = 0
      } else {
        this.groupActive = true
        this.groupCoeffs = hipsOffset.coeffs
        this.groupX0 = hipsOffset.coeffs.x0
      }
    } else {
      this.groupActive = false
      this.groupCoeffs = null
      this.groupX0 = 0
    }

    // Immediately re-apply src pose to avoid pop (x(0)=x0)
    // This overwrites the dst pose that mixer just wrote, restoring src for this frame.
    // Subsequent update() calls will decay toward dst.
    this.apply(0)
    // At t=0 the complementary term is 0, so the group sits at base+accum.
    this.writeGroupXY(0)

    if (debugEnabled) {
      const deg = (r: number) => ((r * 180) / Math.PI).toFixed(1)
      // Naming the worst bones is the whole point: a transition that looks
      // wrong is otherwise impossible to attribute from a duration alone.
      const worst = this.offsets
        .map((o, i) => [this.boneNames[i], o.coeffs.x0] as const)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 4)
        .map(([n, x]) => `${n}=${deg(x)}°`)
        .join(' ')
      console.debug(
        `[inertial] θ=${deg(theta)}° ceiling=${maxSec.toFixed(2)}s ` +
          `T=${T.toFixed(3)}s stagger=${this.activeStagger.toFixed(3)}s ` +
          `total=${this.duration.toFixed(3)}s depth=${this.maxDepth} ` +
          `bones=${this.offsets.filter((o) => o.coeffs.x0 > 1e-6).length}/${this.bones.length}` +
          (this.groupActive ? ` root=${this.groupPending.length().toFixed(4)}m` : '') +
          `\n           worst: ${worst}`,
      )
    }
  }

  /**
   * Write the group's horizontal offset, `fraction` of the pending displacement
   * folded in on top of what previous blends already accumulated.
   *
   * X and Y ONLY. The scene is Z-up and `GroundClamp` owns world Z: it caches
   * its own `lift` and skips the write when the lift is stable, so anything
   * else touching Z leaves the character pinned at a stale height and corrupts
   * the `rawLowest = lowest - lift` baseline it measures against
   * (see groundClamp.ts:96-100). `RootMotionAccumulator` splits the axes the
   * same way, and for the same reason.
   */
  private writeGroupXY(fraction: number): void {
    if (!this.groupTarget) return
    this.groupTarget.position.x = this.groupBase.x + this.groupAccum.x + this.groupPending.x * fraction
    this.groupTarget.position.y = this.groupBase.y + this.groupAccum.y + this.groupPending.y * fraction
  }

  /**
   * Per-frame update. Must be called right after mixer.update(delta) and before vrm.update().
   * Writes blended pose onto bone normalized nodes.
   * Captures raw dst before blending for next frame's restoreRaw.
   */
  update(delta: number): void {
    if (!this.active) return
    // Capture raw dst after mixer (bones currently hold raw dst because restoreRaw was called before mixer)
    for (let i = 0; i < this.bones.length; i++) {
      this.rawDstQuats[i].copy(this.bones[i].quaternion)
    }
    if (this.hipsNode) {
      this.rawDstHipsPos.copy(this.hipsNode.position)
    }

    this.elapsed += delta
    if (this.elapsed >= this.duration) {
      // Finished — ensure clean state (offset 0 → exactly dst pose, which mixer already holds)
      // Need to ensure bones are at raw dst, not blended. Since we captured raw before blending,
      // the bones currently hold raw dst from mixer, but we haven't blended this frame yet.
      // We should leave them as raw dst.
      this.active = false
      // Fold group pending into accum
      if (this.groupActive && this.groupTarget) {
        this.groupAccum.add(this.groupPending)
        this.groupPending.set(0, 0, 0)
        this.writeGroupXY(0)
        this.groupActive = false
        this.groupCoeffs = null
      }
      this.offsets = []
      this.hipsOffset = null
      this.rawDstValid = false
      return
    }
    this.apply(this.elapsed)
  }

  private apply(t: number): void {
    // For each bone, q_final = q_off(t) * q_dst_raw
    for (let i = 0; i < this.bones.length; i++) {
      const offset = this.offsets[i]
      if (!offset || offset.coeffs.x0 < 1e-9) continue
      // Use rawDstQuats as dst, not current bone (which may be blended)
      const currDst = this.rawDstQuats[i]
      // Staggered clock: before its turn `t - delay` is negative and
      // evalInertialX returns x0, so the bone simply holds its source pose.
      const x = evalInertialX(t - this.delays[i], offset.coeffs)
      if (x < 1e-6) continue // offset decayed
      // axis-angle → quat (reuse scratchQuat)
      const half = x / 2
      const s = Math.sin(half)
      this.scratchQuat.set(offset.axis.x * s, offset.axis.y * s, offset.axis.z * s, Math.cos(half))
      // q_final = q_off * q_dst
      this.scratchQuat2.copy(this.scratchQuat).multiply(currDst)
      this.bones[i].quaternion.copy(this.scratchQuat2)
    }

    // Hips position runs UNSTAGGERED, on the same clock as the group below.
    // The two are what cancel to keep the character still in world space, so
    // giving either one a head start would reintroduce the displacement pop.
    // Hips is depth 0 anyway; this is spelled out so a later change to
    // `hopsToAncestor` cannot silently break the root-motion invariant.
    // Hips position: pos_final = dstPos + dir * x(t)
    // Only apply if not handling group via complementary (group will handle world). But we still need hips local decay
    // For root-motion case, hips local should still decay, group will complement. So keep both.
    if (this.hipsOffset && this.hipsNode) {
      const x = evalInertialX(t, this.hipsOffset.coeffs)
      if (x >= 1e-6) {
        this.scratchVec.copy(this.hipsOffset.dir).multiplyScalar(x)
        // currDstPos is rawDstHipsPos
        this.scratchVec2.copy(this.rawDstHipsPos).add(this.scratchVec)
        this.hipsNode.position.copy(this.scratchVec2)
      } else {
        this.hipsNode.position.copy(this.rawDstHipsPos)
      }
    }

    // Group takes over exactly as fast as the hips offset lets go, so the two
    // sum to a constant and the character does not move in world space.
    if (this.groupActive && this.groupTarget && this.groupCoeffs) {
      const x = evalInertialX(t, this.groupCoeffs)
      const ratio = this.groupX0 > 1e-9 ? 1 - x / this.groupX0 : 1
      this.writeGroupXY(Math.max(0, Math.min(1, ratio)))
    }
  }

  cancel(): void {
    this.active = false
    this.offsets = []
    this.hipsOffset = null
    this.elapsed = 0
    this.duration = 0
    this.rawDstValid = false
    // Keep group accum but clear pending
    this.groupActive = false
    this.groupCoeffs = null
    this.groupPending.set(0, 0, 0)
  }

  /** Stagger actually applied to the deepest bone, in seconds (diagnostics). */
  get activeStagger(): number {
    return this.delays.reduce((a, b) => Math.max(a, b), 0)
  }

  dispose(): void {
    this.cancel()
    this.bones.length = 0
    this.prevQuats.length = 0
    this.currQuats.length = 0
    this.rawDstQuats.length = 0
  }
}

/**
 * Parent hops from `node` up to `ancestor`. Returns 0 when `ancestor` is null
 * or is not on the node's parent chain — "unknown depth" collapses to "leads
 * the chain", which is the safe answer for a stagger.
 */
function hopsToAncestor(node: THREE.Object3D, ancestor: THREE.Object3D | null): number {
  if (!ancestor || node === ancestor) return 0
  let hops = 0
  let cur: THREE.Object3D | null = node
  while (cur && cur !== ancestor && hops < 64) {
    cur = cur.parent
    hops++
  }
  return cur === ancestor ? hops : 0
}
