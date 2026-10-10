import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { VRMHumanBoneName } from '@pixiv/three-vrm'
import { BreathingLayer, HOLD_DELAY_S, type HumanoidLike } from './BreathingLayer'

function rig() {
  const nodes = new Map<VRMHumanBoneName, THREE.Object3D>()
  for (const name of Object.values(VRMHumanBoneName)) nodes.set(name, new THREE.Object3D())
  const humanoid: HumanoidLike = { getNormalizedBoneNode: (n) => nodes.get(n) ?? null }
  return { nodes, humanoid }
}

const DT = 1 / 60
const run = (layer: BreathingLayer, seconds: number, forced = false, beforeEach?: (t: number) => void) => {
  for (let t = 0; t < seconds; t += DT) {
    beforeEach?.(t)
    layer.update(DT, forced)
  }
}

describe('BreathingLayer', () => {
  it('fades in once the pose has been held still', () => {
    const { humanoid } = rig()
    const layer = new BreathingLayer(humanoid)
    run(layer, HOLD_DELAY_S * 0.5)
    expect(layer.weight).toBe(0)
    run(layer, 1.5)
    expect(layer.weight).toBeCloseTo(1)
  })

  it('stays off while the animation is clearly moving', () => {
    const { humanoid, nodes } = rig()
    const layer = new BreathingLayer(humanoid)
    const arm = nodes.get(VRMHumanBoneName.RightUpperArm)!
    // A mixer writing a fresh, moving rotation every frame (≈30°/s).
    run(layer, 2, false, (t) => arm.quaternion.setFromEuler(new THREE.Euler(0, 0, t * 0.5)))
    expect(layer.weight).toBe(0)
  })

  it('is on from the start in states that ask for it', () => {
    const { humanoid } = rig()
    const layer = new BreathingLayer(humanoid)
    run(layer, 0.7, true)
    expect(layer.weight).toBeCloseTo(1)
  })

  it('actually moves the chest, and the shoulders symmetrically', () => {
    const { humanoid, nodes } = rig()
    const layer = new BreathingLayer(humanoid)
    run(layer, 3, true) // mid-breath
    const chest = nodes.get(VRMHumanBoneName.Chest)!.quaternion
    expect(chest.angleTo(new THREE.Quaternion())).toBeGreaterThan(0.001)
    const l = new THREE.Euler().setFromQuaternion(nodes.get(VRMHumanBoneName.LeftShoulder)!.quaternion)
    const r = new THREE.Euler().setFromQuaternion(nodes.get(VRMHumanBoneName.RightShoulder)!.quaternion)
    expect(l.z).toBeCloseTo(-r.z, 6)
  })

  it('never accumulates on a bone the mixer does not rewrite', () => {
    const { humanoid, nodes } = rig()
    const layer = new BreathingLayer(humanoid)
    const chest = nodes.get(VRMHumanBoneName.Chest)!
    // Nothing writes the bones: after any number of full breaths the chest
    // must be back at its rest rotation whenever the breath is at zero.
    run(layer, 4.2 * 5, true)
    let maxAngle = 0
    run(layer, 4.2 * 3, true, () => {
      maxAngle = Math.max(maxAngle, chest.quaternion.angleTo(new THREE.Quaternion()))
    })
    expect(maxAngle).toBeLessThan(2 * (Math.PI / 180)) // bounded by one breath, not growing
  })

  it('leaves the head alone (HeadController owns it)', () => {
    const { humanoid, nodes } = rig()
    const layer = new BreathingLayer(humanoid)
    run(layer, 3, true)
    expect(nodes.get(VRMHumanBoneName.Head)!.quaternion.angleTo(new THREE.Quaternion())).toBe(0)
  })

  it('fades out quickly once the state stops asking and motion resumes', () => {
    const { humanoid, nodes } = rig()
    const layer = new BreathingLayer(humanoid)
    run(layer, 1, true)
    const arm = nodes.get(VRMHumanBoneName.RightUpperArm)!
    run(layer, 0.5, false, (t) => arm.quaternion.setFromEuler(new THREE.Euler(0, 0, t * 2)))
    expect(layer.weight).toBe(0)
  })
})
