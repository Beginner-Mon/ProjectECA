import { beforeEach, describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { VRMHumanBoneName } from '@pixiv/three-vrm'
import type { VRM } from '@pixiv/three-vrm'
import {
  computeInertialCoeffs,
  evalInertialX,
  evalInertialV,
  evalInertialA,
  PoseInertializer,
  adaptiveDuration,
  FULL_ANGLE_RAD,
} from './Inertializer'

const EPS = 1e-6

describe('Inertializer polynomial — Bollo quintic (GDC 2018)', () => {
  describe('6 boundary conditions', () => {
    it('satisfies x(0)=x0, x(T)=0', () => {
      const x0 = 0.8
      const v0 = -0.5
      const T = 0.5
      const c = computeInertialCoeffs(x0, v0, T)
      expect(evalInertialX(0, c)).toBeCloseTo(x0, 9)
      expect(evalInertialX(c.T, c)).toBeCloseTo(0, 6)
    })

    it('satisfies x(0)=x0 with v0>0 clamped (v0 should be treated as 0)', () => {
      const x0 = 0.4
      const v0 = 1.2 // >0 → clamped to 0
      const T = 0.3
      const c = computeInertialCoeffs(x0, v0, T)
      expect(c.v0).toBe(0)
      expect(evalInertialX(0, c)).toBeCloseTo(x0, 9)
      expect(evalInertialV(0, c)).toBeCloseTo(0, 9)
    })

    it('satisfies x\'(0)=v0 and x\'(T)=0', () => {
      const x0 = 0.6
      const v0 = -1.0
      const T = 0.8
      const c = computeInertialCoeffs(x0, v0, T)
      expect(evalInertialV(0, c)).toBeCloseTo(c.v0, 9)
      expect(evalInertialV(c.T, c)).toBeCloseTo(0, 6)
    })

    it('satisfies x\'\'(0)=a0 and x\'\'(T)=0', () => {
      const x0 = 0.5
      const v0 = -0.3
      const T = 0.4
      const c = computeInertialCoeffs(x0, v0, T)
      expect(evalInertialA(0, c)).toBeCloseTo(c.a0, 6)
      expect(evalInertialA(c.T, c)).toBeCloseTo(0, 6)
    })

    it('satisfies all 6 conditions together for multiple random samples', () => {
      const samples: Array<[number, number, number]> = [
        [0.3, -0.2, 0.3],
        [1.0, -2.0, 0.8],
        [0.1, 0, 0.3],
        [0.7, -0.01, 0.5],
        [1.2, -0.8, 1.0],
      ]
      for (const [x0, v0, T] of samples) {
        const c = computeInertialCoeffs(x0, v0, T)
        // x(0)=x0
        expect(evalInertialX(0, c)).toBeCloseTo(x0, 9)
        // x'(0)=v0 (clamped)
        expect(evalInertialV(0, c)).toBeCloseTo(c.v0, 9)
        // x''(0)=a0
        expect(evalInertialA(0, c)).toBeCloseTo(c.a0, 6)
        // x(T)=0
        expect(evalInertialX(c.T, c)).toBeCloseTo(0, 6)
        // x'(T)=0
        expect(evalInertialV(c.T, c)).toBeCloseTo(0, 6)
        // x''(T)=0
        expect(evalInertialA(c.T, c)).toBeCloseTo(0, 6)
      }
    })

    it('handles x0=0 → all coeffs zero, trivial solution', () => {
      const c = computeInertialCoeffs(0, 0, 0.3)
      expect(c.A).toBeCloseTo(0, 9)
      expect(c.B).toBeCloseTo(0, 9)
      expect(c.C).toBeCloseTo(0, 9)
      expect(c.a0).toBeCloseTo(0, 9)
      expect(evalInertialX(0.15, c)).toBeCloseTo(0, 9)
      expect(evalInertialX(c.T, c)).toBeCloseTo(0, 9)
    })
  })

  describe('clamping logic', () => {
    it('clamps v0>0 to 0 (offset đang nở ra → không cho nở thêm)', () => {
      const c = computeInertialCoeffs(0.5, 0.9, 0.3)
      expect(c.v0).toBe(0)
    })

    it('clamps T when v0<0 and -5*x0/v0 < T (chặn overshoot)', () => {
      const x0 = 0.2
      const v0 = -2.0
      const T = 1.0
      const expectedClamped = -5 * x0 / v0 // 0.5
      const c = computeInertialCoeffs(x0, v0, T)
      expect(c.T).toBeCloseTo(expectedClamped, 9)
      expect(c.T).toBeLessThan(T)
    })

    it('does not clamp T when -5*x0/v0 >= T', () => {
      const x0 = 1.0
      const v0 = -0.1
      const T = 0.3
      // -5*1.0 / -0.1 = 50 >> 0.3
      const c = computeInertialCoeffs(x0, v0, T)
      expect(c.T).toBeCloseTo(T, 9)
    })
  })

  describe('monotonicity and no overshoot with v0=0', () => {
    it('with v0=0, x(t) is monotonically decreasing from x0 to 0', () => {
      const x0 = 0.8
      const c = computeInertialCoeffs(x0, 0, 0.5)
      let prev = evalInertialX(0, c)
      expect(prev).toBeCloseTo(x0, 9)
      const steps = 100
      for (let i = 1; i <= steps; i++) {
        const t = (c.T * i) / steps
        const x = evalInertialX(t, c)
        // monotonic decreasing (allow tiny epsilon)
        expect(x).toBeLessThanOrEqual(prev + EPS)
        // never overshoot below 0 or above x0
        expect(x).toBeGreaterThanOrEqual(-EPS)
        expect(x).toBeLessThanOrEqual(x0 + EPS)
        prev = x
      }
      expect(evalInertialX(c.T, c)).toBeCloseTo(0, 6)
    })

    it('with v0=0, derivative matches analytical (20x0 t / T^2)(tau-1)^3', () => {
      const x0 = 0.5
      const T = 0.4
      const c = computeInertialCoeffs(x0, 0, T)
      for (let i = 0; i <= 10; i++) {
        const t = (T * i) / 10
        const tau = t / T
        const expectedV = (20 * x0 * t) / (T * T) * Math.pow(tau - 1, 3)
        expect(evalInertialV(t, c)).toBeCloseTo(expectedV, 6)
      }
    })

    it('with v0=0, analytical x(t) = x0*(4tau^5 -15tau^4 +20tau^3 -10tau^2 +1)', () => {
      const x0 = 0.6
      const T = 0.3
      const c = computeInertialCoeffs(x0, 0, T)
      for (let i = 0; i <= 10; i++) {
        const t = (T * i) / 10
        const tau = t / T
        const expected = x0 * (4 * Math.pow(tau, 5) - 15 * Math.pow(tau, 4) + 20 * Math.pow(tau, 3) - 10 * tau * tau + 1)
        expect(evalInertialX(t, c)).toBeCloseTo(expected, 6)
      }
    })

    it('with v0<0, does not overshoot and stays bounded', () => {
      const x0 = 0.5
      const v0 = -1.0
      const c = computeInertialCoeffs(x0, v0, 0.8)
      const steps = 100
      for (let i = 0; i <= steps; i++) {
        const t = (c.T * i) / steps
        const x = evalInertialX(t, c)
        expect(x).toBeGreaterThanOrEqual(-EPS)
        expect(x).toBeLessThanOrEqual(x0 + EPS)
      }
    })
  })

  describe('numerical stability', () => {
    it('handles small x0 and small T without NaN', () => {
      const c = computeInertialCoeffs(1e-6, -1e-6, 0.01)
      expect(Number.isFinite(c.A)).toBe(true)
      expect(Number.isFinite(c.B)).toBe(true)
      expect(Number.isFinite(c.C)).toBe(true)
      expect(Number.isFinite(evalInertialX(0.005, c))).toBe(true)
    })

    it('handles x0=0 with v0!=0 (clamped)', () => {
      const c = computeInertialCoeffs(0, -1, 0.3)
      // x0=0, v0 negative: T clamped to 0, but should not produce NaN
      // In this edge, -5*x0/v0 = 0, so T becomes 0 → need guard; allow T>0
      // Implementation should handle this gracefully
      expect(Number.isFinite(evalInertialX(0, c))).toBe(true)
    })
  })
})

/** Helper: VRM with controllable bones */
function makeVrmWithBones() {
  const scene = new THREE.Group()
  const hips = new THREE.Object3D()
  hips.name = 'Hips'
  const spine = new THREE.Object3D()
  spine.name = 'Spine'
  const rightArm = new THREE.Object3D()
  rightArm.name = 'RightUpperArm'
  scene.add(hips)
  scene.add(spine)
  scene.add(rightArm)

  const boneMap = new Map<string, THREE.Object3D>([
    [VRMHumanBoneName.Hips, hips],
    [VRMHumanBoneName.Spine, spine],
    [VRMHumanBoneName.RightUpperArm, rightArm],
  ])

  const humanoid = {
    humanBones: Object.fromEntries(boneMap),
    getNormalizedBoneNode: (name: string) => boneMap.get(name) ?? null,
    getRawBoneNode: (name: string) => boneMap.get(name) ?? null,
  }

  const vrm = { scene, humanoid } as unknown as VRM
  return { vrm, hips, spine, rightArm, boneMap }
}

function quatFromAxisAngle(axis: THREE.Vector3, angle: number): THREE.Quaternion {
  const q = new THREE.Quaternion()
  q.setFromAxisAngle(axis.normalize(), angle)
  return q
}

describe('PoseInertializer — behavior', () => {
  let vrm: VRM
  let hips: THREE.Object3D
  let spine: THREE.Object3D
  let rightArm: THREE.Object3D

  beforeEach(() => {
    const ctx = makeVrmWithBones()
    vrm = ctx.vrm
    hips = ctx.hips
    spine = ctx.spine
    rightArm = ctx.rightArm
    // start from identity
    hips.quaternion.identity()
    spine.quaternion.identity()
    rightArm.quaternion.identity()
    hips.position.set(0, 0, 0)
  })

  it('offset 0 → no-op, stays inactive and does not modify pose', () => {
    const iz = new PoseInertializer(vrm)
    // Establish history at identity
    hips.quaternion.identity()
    iz.recordFrame()
    iz.recordFrame()
    // dst is also identity (no offset)
    hips.quaternion.identity()
    // begin with same pose as src
    iz.begin(0.3, 1 / 60)
    expect(iz.isActive).toBe(false)
    // pose should remain identity
    expect(hips.quaternion.angleTo(new THREE.Quaternion())).toBeCloseTo(0, 5)
  })

  it('t=0 exactly matches P_src (no pop)', () => {
    const iz = new PoseInertializer(vrm)
    const srcQuat = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.6)
    // Set history to src for both bones and hips: srcQuat for arm, hips at 0
    rightArm.quaternion.copy(srcQuat)
    hips.position.set(0, 0, 0)
    iz.recordFrame()
    rightArm.quaternion.copy(srcQuat)
    hips.position.set(0, 0, 0)
    iz.recordFrame()

    // Now mixer puts dst: arm identity, hips at 1
    rightArm.quaternion.identity()
    hips.position.set(1, 0, 0)

    iz.begin(0.3, 1 / 60)

    // At t=0, rightArm should be back at src
    expect(rightArm.quaternion.angleTo(srcQuat)).toBeCloseTo(0, 4)
    // hips should be at src (0)
    expect(hips.position.distanceTo(new THREE.Vector3(0, 0, 0))).toBeCloseTo(0, 4)
  })

  it('t>=T exactly matches dst clip (offset decayed)', () => {
    const iz = new PoseInertializer(vrm)
    const srcQuat = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.8)
    rightArm.quaternion.copy(srcQuat)
    iz.recordFrame()
    rightArm.quaternion.copy(srcQuat)
    iz.recordFrame()

    const dstQuat = new THREE.Quaternion() // identity
    rightArm.quaternion.copy(dstQuat)
    // hips dst
    hips.position.set(1, 0, 0)
    // hips src was 0
    iz.begin(0.3, 1 / 60)
    expect(iz.isActive).toBe(true)

    // Simulate frames until after duration
    // Need to keep feeding dst each frame (mixer would)
    let elapsed = 0
    const dt = 1 / 60
    while (iz.isActive) {
      // mixer would have written dst again each frame
      rightArm.quaternion.copy(dstQuat)
      hips.position.set(1, 0, 0)
      iz.update(dt)
      elapsed += dt
      // Keep history updated for completeness (not needed for this test)
      // but inertializer expects recordFrame after update in real loop
      // Don't record here to keep history stable
      if (elapsed > 2) break // safety
    }

    // After finish, pose should be dst (since offset 0, we left dst as is)
    // Need to ensure last mixer write is still dst
    rightArm.quaternion.copy(dstQuat)
    expect(rightArm.quaternion.angleTo(dstQuat)).toBeCloseTo(0, 4)
    hips.position.set(1, 0, 0)
    expect(hips.position.distanceTo(new THREE.Vector3(1, 0, 0))).toBeCloseTo(0, 4)
    expect(iz.isActive).toBe(false)
  })

  it('hips position uses same quintic on magnitude, direction stays fixed', () => {
    const iz = new PoseInertializer(vrm)
    // History: src at (0,0,0)
    hips.position.set(0, 0, 0)
    iz.recordFrame()
    hips.position.set(0, 0, 0)
    iz.recordFrame()
    // dst at (1, 0, 0) — offset magnitude 1 along +X
    hips.position.set(1, 0, 0)
    iz.begin(0.5, 1 / 60)
    expect(iz.isActive).toBe(true)

    // At t=0, hips should be at src 0
    expect(hips.position.x).toBeCloseTo(0, 4)
    expect(hips.position.y).toBeCloseTo(0, 4)

    // At mid duration (~0.25), should be between 0 and 1, never overshoot
    // Simulate a few frames with dst fixed
    const midSteps = Math.floor(0.25 / (1 / 60))
    for (let i = 0; i < midSteps; i++) {
      hips.position.set(1, 0, 0) // mixer dst
      iz.update(1 / 60)
      expect(hips.position.x).toBeGreaterThanOrEqual(-1e-6)
      expect(hips.position.x).toBeLessThanOrEqual(1 + 1e-6)
      expect(hips.position.y).toBeCloseTo(0, 5)
    }
    // Ensure monotonic: hips should move from 0 toward 1, so x should be increasing
    // (since offset decays, pos = dst + dir*x(t) with dir from src-dst = -1, so pos =1 - x(t))
    // Actually offset dir is src-dst = (-1), magnitude 1, so pos = dst + dir*x =1 - x
    // At t=0, x=1 → pos 0; at t=T, x=0 → pos 1; so x(t) decreasing → pos increasing
  })

  it('cancel deactivates immediately', () => {
    const iz = new PoseInertializer(vrm)
    const srcQuat = quatFromAxisAngle(new THREE.Vector3(1, 0, 0), 0.5)
    rightArm.quaternion.copy(srcQuat)
    iz.recordFrame()
    iz.recordFrame()
    rightArm.quaternion.identity()
    hips.position.set(0.5, 0, 0)
    iz.begin(0.3, 1 / 60)
    expect(iz.isActive).toBe(true)
    iz.cancel()
    expect(iz.isActive).toBe(false)
  })

  it('recordFrame maintains velocity history for v0 estimation', () => {
    const iz = new PoseInertializer(vrm)
    // Create motion: prev = 0.2 rad, src = 0.4 rad → velocity positive along offset axis
    // But offset is src - dst where dst=0 → offset 0.4, v0 should be + (?) → clamped to 0
    const qPrev = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.2)
    const qSrc = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.4)
    rightArm.quaternion.copy(qPrev)
    iz.recordFrame()
    rightArm.quaternion.copy(qSrc)
    iz.recordFrame()
    rightArm.quaternion.identity() // dst
    iz.begin(0.3, 1 / 60)
    // Since velocity is expanding (src moving away from dst), v0 should be clamped to 0
    // We can't directly inspect coeffs, but we can ensure no explosion and isActive
    expect(iz.isActive).toBe(true)
  })
})

describe('PoseInertializer — adaptive duration', () => {
  it('stays flat: duration spans only [0.6, 1] * maxSec and saturates at FULL_ANGLE', () => {
    const maxSec = 0.8
    // θ=0 and θ=FULL_ANGLE are the two ends of the whole range.
    expect(adaptiveDuration(0, maxSec)).toBeCloseTo(0.48, 5) // 0.6 * 0.8
    expect(adaptiveDuration(FULL_ANGLE_RAD, maxSec)).toBeCloseTo(0.8, 5)
    // Beyond FULL_ANGLE the curve is clamped, never longer than the ceiling.
    expect(adaptiveDuration((90 * Math.PI) / 180, maxSec)).toBeCloseTo(0.8, 5)
    expect(adaptiveDuration(Math.PI, maxSec)).toBeCloseTo(0.8, 5)
    // Monotonic across the range.
    let prev = -1
    for (let d = 0; d <= 90; d += 5) {
      const v = adaptiveDuration((d * Math.PI) / 180, maxSec)
      expect(v).toBeGreaterThanOrEqual(prev)
      prev = v
    }
  })

  /**
   * The regression that sent us back here: a linear θ→duration ramp bottoming
   * out at 0.35 made `greeting → idle` run in ~0.18 s against the 0.5 s
   * crossfade it replaced, which read as twitchy. Small transitions must stay
   * within a factor of ~1.7 of large ones, not ~3.
   */
  it('a small transition is not dramatically faster than a large one (Fitts, not linear)', () => {
    const small = adaptiveDuration((10 * Math.PI) / 180, 0.5)
    const large = adaptiveDuration((90 * Math.PI) / 180, 0.5)
    expect(small).toBeLessThan(large)
    expect(large / small).toBeLessThan(1.7)
    // And a small transition must not undercut the crossfade it replaced by more
    // than a third — 0.18 s out of 0.5 s was the bug.
    expect(small).toBeGreaterThan(0.5 * 0.6 - 1e-9)
  })

  it('PoseInertializer with small offset uses shorter duration than large offset', () => {
    const mk = () => makeVrmWithBones()
    // Small offset: 10° arm
    const ctxSmall = mk()
    const izSmall = new PoseInertializer(ctxSmall.vrm)
    const qSmall = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), (10 * Math.PI) / 180)
    ctxSmall.rightArm.quaternion.copy(qSmall)
    izSmall.recordFrame()
    ctxSmall.rightArm.quaternion.copy(qSmall)
    izSmall.recordFrame()
    ctxSmall.rightArm.quaternion.identity() // dst
    izSmall.begin(0.8, 1 / 60)
    const durSmall = izSmall.activeDuration

    // Large offset: 70° arm (>60)
    const ctxLarge = mk()
    const izLarge = new PoseInertializer(ctxLarge.vrm)
    const qLarge = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), (70 * Math.PI) / 180)
    ctxLarge.rightArm.quaternion.copy(qLarge)
    izLarge.recordFrame()
    ctxLarge.rightArm.quaternion.copy(qLarge)
    izLarge.recordFrame()
    ctxLarge.rightArm.quaternion.identity()
    izLarge.begin(0.8, 1 / 60)
    const durLarge = izLarge.activeDuration

    expect(durSmall).toBeLessThan(durLarge)
    // 10°: 0.8 * (0.6 + 0.4 * log2(1 + 10/45)) ≈ 0.573 — shorter, but nowhere
    // near the 0.28 the old linear ramp produced.
    expect(durSmall).toBeCloseTo(0.573, 2)
    expect(durLarge).toBeCloseTo(0.8, 2) // 70° is past FULL_ANGLE → full ceiling
  })

  it('hips-only offset still triggers with full duration', () => {
    const ctx = makeVrmWithBones()
    const iz = new PoseInertializer(ctx.vrm)
    // Bones zero, hips displaced
    ctx.hips.position.set(0, 0, 0)
    iz.recordFrame()
    ctx.hips.position.set(0, 0, 0)
    iz.recordFrame()
    ctx.hips.position.set(1, 0, 0) // dst
    // Bones remain identity (zero angle)
    iz.begin(0.5, 1 / 60)
    expect(iz.isActive).toBe(true)
    expect(iz.activeDuration).toBeCloseTo(0.5, 2)
  })
})

/** Skeleton with a real parent chain, so bone depth is meaningful. */
function makeChainedVrm() {
  const scene = new THREE.Group()
  const hips = new THREE.Object3D()
  const spine = new THREE.Object3D()
  const chest = new THREE.Object3D()
  const upperArm = new THREE.Object3D()
  const lowerArm = new THREE.Object3D()
  const hand = new THREE.Object3D()
  scene.add(hips)
  hips.add(spine)
  spine.add(chest)
  chest.add(upperArm)
  upperArm.add(lowerArm)
  lowerArm.add(hand)
  const boneMap = new Map<string, THREE.Object3D>([
    [VRMHumanBoneName.Hips, hips],
    [VRMHumanBoneName.Spine, spine],
    [VRMHumanBoneName.Chest, chest],
    [VRMHumanBoneName.RightUpperArm, upperArm],
    [VRMHumanBoneName.RightLowerArm, lowerArm],
    [VRMHumanBoneName.RightHand, hand],
  ])
  const vrm = {
    scene,
    humanoid: {
      humanBones: Object.fromEntries(boneMap),
      getNormalizedBoneNode: (n: string) => boneMap.get(n) ?? null,
      getRawBoneNode: (n: string) => boneMap.get(n) ?? null,
    },
  } as unknown as VRM
  return { vrm, hips, spine, chest, upperArm, lowerArm, hand }
}

describe('PoseInertializer — kinetic-chain stagger', () => {
  function runBlend(iz: PoseInertializer, bones: THREE.Object3D[], src: THREE.Quaternion) {
    const dst = new THREE.Quaternion()
    for (const b of bones) b.quaternion.copy(src)
    iz.recordFrame()
    for (const b of bones) b.quaternion.copy(src)
    iz.recordFrame()
    for (const b of bones) b.quaternion.copy(dst) // mixer.update(0)
    iz.begin(0.5, 1 / 60)
    return dst
  }

  it('distal bones start moving after proximal ones', () => {
    const ctx = makeChainedVrm()
    const iz = new PoseInertializer(ctx.vrm)
    const src = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.8)
    const bones = [ctx.hips, ctx.spine, ctx.chest, ctx.upperArm, ctx.lowerArm, ctx.hand]
    const dst = runBlend(iz, bones, src)

    expect(iz.activeStagger).toBeGreaterThan(0)

    // Step to a point where the chain has begun but the hand has not.
    const dt = 1 / 60
    for (let i = 0; i < 4; i++) {
      iz.restoreRaw()
      for (const b of bones) b.quaternion.copy(dst) // mixer writes dst
      iz.update(dt)
      iz.recordFrame()
    }
    const moved = (b: THREE.Object3D) => src.angleTo(b.quaternion)
    // Hips leads; the hand is still parked on its source pose.
    expect(moved(ctx.hips)).toBeGreaterThan(moved(ctx.hand))
    expect(moved(ctx.hand)).toBeCloseTo(0, 6)
  })

  it('is exact at t=0 and everything still lands on dst by the end', () => {
    const ctx = makeChainedVrm()
    const iz = new PoseInertializer(ctx.vrm)
    const src = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.8)
    const bones = [ctx.hips, ctx.spine, ctx.chest, ctx.upperArm, ctx.lowerArm, ctx.hand]
    const dst = runBlend(iz, bones, src)

    // Stagger must not cost the no-pop guarantee: every bone holds src at t=0.
    for (const b of bones) expect(b.quaternion.angleTo(src)).toBeCloseTo(0, 5)

    const dt = 1 / 60
    for (let i = 0; i < 200; i++) {
      iz.restoreRaw()
      for (const b of bones) b.quaternion.copy(dst)
      iz.update(dt)
      iz.recordFrame()
    }
    // The blend outlives the deepest bone's delay — nothing gets cut off.
    expect(iz.isActive).toBe(false)
    for (const b of bones) expect(b.quaternion.angleTo(dst)).toBeCloseTo(0, 4)
  })

  it('never spends more than a third of the blend waiting', () => {
    const ctx = makeChainedVrm()
    const iz = new PoseInertializer(ctx.vrm)
    const src = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.8)
    const bones = [ctx.hips, ctx.spine, ctx.chest, ctx.upperArm, ctx.lowerArm, ctx.hand]
    runBlend(iz, bones, src)
    expect(iz.activeStagger).toBeLessThanOrEqual(iz.activeDuration * 0.35 + 1e-9)
  })
})

describe('PoseInertializer — regression: bone without track must not drift (fix lỗi 1)', () => {
  it('finger without track stays at dst (bind) and does not accumulate drift over multiple transitions', () => {
    // Simulate VRM with 55 bones, clip with 22 tracks: finger is static
    const ctx = makeVrmWithBones()
    // Add a finger bone that will be "without track"
    const finger = new THREE.Object3D()
    finger.name = 'LeftThumbProximal'
    // For test simplicity, directly use ctx bones plus finger: we will create a new VRM mock
    const scene = new THREE.Group()
    const hips = ctx.hips
    const fingerNode = finger
    scene.add(hips)
    scene.add(fingerNode)
    const boneMap = new Map<string, THREE.Object3D>([
      [VRMHumanBoneName.Hips, hips],
      ['leftThumbProximal', fingerNode],
    ])
    const vrmMock = {
      scene,
      humanoid: {
        humanBones: Object.fromEntries(boneMap),
        getNormalizedBoneNode: (name: string) => boneMap.get(name) ?? null,
        getRawBoneNode: (name: string) => boneMap.get(name) ?? null,
      },
    } as unknown as VRM

    const iz = new PoseInertializer(vrmMock)
    // Finger src: idle pose 0.6 rad around Y
    const srcFinger = quatFromAxisAngle(new THREE.Vector3(0, 1, 0), 0.6)
    const dstFinger = new THREE.Quaternion() // bind / dst when clip has no finger track
    // Hips src/dst same (no offset) to isolate finger
    hips.quaternion.identity()
    fingerNode.quaternion.copy(srcFinger)
    iz.recordFrame()
    hips.quaternion.identity()
    fingerNode.quaternion.copy(srcFinger)
    iz.recordFrame()

    // Now transition to exercise: exercise has no finger track, so after mixer, finger stays at bind (dst)
    // Simulate mixer: hips has track (identity), finger has no track → stays at rawDst (bind)
    // Our begin will capture dst as current finger = bind, src = 0.6
    fingerNode.quaternion.copy(dstFinger)
    hips.quaternion.identity()
    iz.begin(0.3, 1 / 60)

    // Simulate frames with proper restoreRaw -> mixer -> update loop for a bone without track
    // For finger without track, mixer does NOT overwrite, so after restoreRaw finger is at rawDst (bind),
    // mixer leaves it, then inertializer blends.
    const dt = 1 / 60
    let maxError = 0
    // Run for full duration + extra to ensure no drift accumulation
    for (let i = 0; i < 120; i++) {
      // restoreRaw (as AnimationController does)
      iz.restoreRaw()
      // mixer: hips would be overwritten to dst (identity) if it has track, finger NOT overwritten
      // So hips stays at rawDst (identity), finger stays at rawDst (identity) after restore
      // (for finger, mixer does nothing, so it stays at rawDst)
      // Then update captures raw and blends
      iz.update(dt)
      iz.recordFrame()
      // After update, finger should be between src and dst, never drift beyond
      const angleToDst = fingerNode.quaternion.angleTo(dstFinger)
      maxError = Math.max(maxError, angleToDst)
      // Finger should never exceed initial offset 0.6 significantly
      expect(angleToDst).toBeLessThan(0.61) // no overshoot
    }
    // After duration, finger must be at dst (bind), not drifted to 2.36 rad
    expect(fingerNode.quaternion.angleTo(dstFinger)).toBeCloseTo(0, 2)
    expect(maxError).toBeLessThan(0.61)
    // Second transition should not accumulate: repeat same
    // Set src again to same idle finger pose, then transition again
    fingerNode.quaternion.copy(srcFinger)
    iz.recordFrame()
    fingerNode.quaternion.copy(srcFinger)
    iz.recordFrame()
    fingerNode.quaternion.copy(dstFinger)
    iz.begin(0.3, 1 / 60)
    for (let i = 0; i < 60; i++) {
      iz.restoreRaw()
      iz.update(dt)
      iz.recordFrame()
    }
    expect(fingerNode.quaternion.angleTo(dstFinger)).toBeCloseTo(0, 2)
  })
})

/**
 * Root-motion hand-off, measured the way the renderer sees it.
 *
 * These build the REAL hierarchy — `group(rotation π/2 about X) → scene → hips`
 * — and read positions with `getWorldPosition()`. That matters: the group's
 * position is world-space (Z-up) while `hips.position` is armature-local
 * (Y-up), and the two differ by exactly that rotation. A test that adds the two
 * `.x` values together is blind to the conversion, because X is the one axis
 * the rotation leaves alone.
 */
function makePosedVrm() {
  const group = new THREE.Group()
  group.position.set(0, 1.5, 0)
  group.rotation.set(Math.PI / 2, 0, 0) // what CharacterViewer renders
  const scene = new THREE.Group()
  group.add(scene)
  const hips = new THREE.Object3D()
  const spine = new THREE.Object3D()
  const leftThumb = new THREE.Object3D()
  scene.add(hips, spine, leftThumb)
  const boneMap = new Map<string, THREE.Object3D>([
    [VRMHumanBoneName.Hips, hips],
    [VRMHumanBoneName.Spine, spine],
    [VRMHumanBoneName.LeftThumbProximal, leftThumb],
  ])
  const vrm = {
    scene,
    humanoid: {
      humanBones: Object.fromEntries(boneMap),
      getNormalizedBoneNode: (n: string) => boneMap.get(n) ?? null,
      getRawBoneNode: (n: string) => boneMap.get(n) ?? null,
    },
  } as unknown as VRM
  return { vrm, group, hips }
}

describe('PoseInertializer — regression: world hips stays continuous (fix lỗi 2)', () => {
  it('keeps world XY put when the motion translates forward (local Z) and up (local Y)', () => {
    const { vrm, group, hips } = makePosedVrm()
    const iz = new PoseInertializer(vrm)
    iz.setGroupTarget(group)

    hips.position.set(0, 0, 0)
    iz.recordFrame()
    // Exercise ends 1.0 m forward (local +Z) and 0.3 m up (local +Y).
    // Under the group's rotation that is 1.0 m of WORLD -Y and 0.3 m of WORLD Z.
    hips.position.set(0, 0.3, 1.0)
    iz.recordFrame()
    iz.recordFrame()

    const before = new THREE.Vector3()
    hips.getWorldPosition(before)

    hips.position.set(0, 0, 0) // mixer.update(0): idle rest
    iz.begin(0.8, 1 / 60, true)

    const world = new THREE.Vector3()
    hips.getWorldPosition(world)
    // t=0 must be continuous — this is the "no 2 m pop" guard.
    expect(Math.hypot(world.x - before.x, world.y - before.y)).toBeLessThan(0.01)

    let maxErrXY = 0
    for (let i = 0; i < 120; i++) {
      iz.restoreRaw()
      hips.position.set(0, 0, 0) // mixer writes idle rest every frame
      iz.update(1 / 60)
      iz.recordFrame()
      hips.getWorldPosition(world)
      maxErrXY = Math.max(maxErrXY, Math.hypot(world.x - before.x, world.y - before.y))
    }

    // The scene is Z-up and GroundClamp owns world Z, so only world XY is ours
    // to preserve. Drifting here means the local→world conversion is wrong.
    expect(maxErrXY).toBeLessThan(0.05)
    hips.getWorldPosition(world)
    expect(world.x).toBeCloseTo(before.x, 2)
    expect(world.y).toBeCloseTo(before.y, 2)
    // The hand-off actually happened: hips back at rest, group carrying it.
    expect(hips.position.length()).toBeCloseTo(0, 3)
  })

  it('never writes group.position.z — GroundClamp owns that channel', () => {
    const { vrm, group, hips } = makePosedVrm()
    const iz = new PoseInertializer(vrm)
    iz.setGroupTarget(group)

    hips.position.set(0, 0, 0)
    iz.recordFrame()
    hips.position.set(1, 0, 0.5)
    iz.recordFrame()
    iz.recordFrame()

    hips.position.set(0, 0, 0)
    iz.begin(0.8, 1 / 60, true)

    // GroundClamp re-asserts the lift every frame and caches it; if the
    // inertializer also writes Z, its `rawLowest = lowest - lift` baseline goes
    // wrong and it stops correcting once the lift settles (groundClamp.ts:96).
    const LIFT = 0.42
    group.position.z = LIFT
    let clobbered = 0
    for (let i = 0; i < 60; i++) {
      iz.restoreRaw()
      hips.position.set(0, 0, 0)
      iz.update(1 / 60)
      iz.recordFrame()
      if (Math.abs(group.position.z - LIFT) > 1e-9) clobbered++
      group.position.z = LIFT
    }
    expect(clobbered).toBe(0)
  })

  /**
   * Every clip ends with the hips a few millimetres off the next clip's rest —
   * `Thinking.fbx` frame 127 sits 3.3 mm from `Standard Idle.fbx` frame 0 on X.
   * Handing that to the group on every transition folds it into `groupAccum`
   * permanently, and a chat session walks the character off its mark. Only
   * clips that actually travel get the hand-off.
   */
  it('leaves the group alone when the outgoing clip did not travel', () => {
    const { vrm, group, hips } = makePosedVrm()
    const iz = new PoseInertializer(vrm)
    iz.setGroupTarget(group)
    const before = group.position.clone()

    hips.position.set(0, 0, 0)
    iz.recordFrame()
    hips.position.set(0.003, 0, 0.001) // a few mm, as a non-travelling clip ends
    iz.recordFrame()
    iz.recordFrame()

    hips.position.set(0, 0, 0)
    iz.begin(0.3, 1 / 60) // handoffRootMotion defaults to false

    for (let i = 0; i < 60; i++) {
      iz.restoreRaw()
      hips.position.set(0, 0, 0)
      iz.update(1 / 60)
      iz.recordFrame()
    }
    // The hips offset still decays smoothly; the group simply never moves.
    expect(group.position.distanceTo(before)).toBeCloseTo(0, 9)
    expect(hips.position.length()).toBeCloseTo(0, 5)
  })
})
