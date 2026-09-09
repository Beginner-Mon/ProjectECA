import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { VRMHumanBoneName } from '@pixiv/three-vrm'
import type { VRM } from '@pixiv/three-vrm'
import { RootMotionAccumulator } from './rootMotionAccumulator'

function makeVrm(hipsPos: THREE.Vector3 = new THREE.Vector3(0, 0, 0)) {
  const hips = new THREE.Object3D()
  hips.position.copy(hipsPos)
  // Make getWorldPosition return hips.position in world (no parent transform)
  const humanoid = {
    getNormalizedBoneNode: (name: VRMHumanBoneName) => {
      if (name === VRMHumanBoneName.Hips) return hips
      return null
    },
  }
  const vrm = { humanoid, scene: new THREE.Group() } as unknown as VRM
  return { vrm, hips }
}

describe('RootMotionAccumulator — crossfade path (ramp)', () => {
  it('ramps offset linearly 0→1 over blendSec so world hips stays constant', () => {
    const target = new THREE.Object3D()
    target.position.set(0, 1.5, 0)
    const acc = new RootMotionAccumulator(target)

    const { vrm: vrmStart } = makeVrm(new THREE.Vector3(0, 0, 0))
    // need to provide same vrm object for begin/commit but hips position changes
    // begin at 0,0,0
    acc.beginOneShot(vrmStart)
    // Simulate end at 1.5m displacement on X
    const { vrm: vrmEnd } = makeVrm(new THREE.Vector3(1.5, 0.7, 0))
    // hips world now at (1.5,0.7)
    acc.commitOneShot(vrmEnd, 0.8)

    // At t=0, group offset should be 0
    expect(target.position.x).toBeCloseTo(0, 5)
    expect(target.position.y).toBeCloseTo(1.5, 5)

    // At half duration, offset should be half
    acc.update(0.4)
    expect(target.position.x).toBeCloseTo(0.75, 5)
    expect(target.position.y).toBeCloseTo(1.5 + 0.35, 5)

    // World hips = group XY + hips local XY? But acc only writes group, hips local stays at 1.5 after clip weight 1→0?
    // For test we check group continuity: at t=0.8, offset fully applied
    acc.update(0.4)
    expect(target.position.x).toBeCloseTo(1.5, 5)
    expect(target.position.y).toBeCloseTo(1.5 + 0.7, 5)

    // After complete, further updates should not change
    acc.update(0.1)
    expect(target.position.x).toBeCloseTo(1.5, 5)
  })

  it('does not accumulate Z (groundClamp owns it)', () => {
    const target = new THREE.Object3D()
    target.position.set(0, 1.5, 0)
    const acc = new RootMotionAccumulator(target)
    const { vrm: vStart } = makeVrm(new THREE.Vector3(0, 0, 0))
    acc.beginOneShot(vStart)
    const { vrm: vEnd } = makeVrm(new THREE.Vector3(0, 0, 2))
    acc.commitOneShot(vEnd, 0.3)
    acc.update(0.3)
    expect(target.position.z).toBeCloseTo(0, 5) // baseZ =0 + lift (groundClamp) not here
  })
})

// Inertial root motion is now handled by PoseInertializer (1 - x/x0) — see Inertializer.test.ts
// This file only covers the crossfade ramp path.
