import * as THREE from 'three'
import { VRMHumanBoneName } from '@pixiv/three-vrm'

/**
 * A slow breath and a faint weight shift layered on top of whatever pose the
 * animation mixer produced this frame (10-10).
 *
 * Why: a held pose — the old frozen thinking frame, the end of a clip — reads as
 * the app hanging, not as a person waiting. A real person is never perfectly
 * still: the chest rises, the shoulders lift a little, the body sways.
 *
 * When it applies:
 *   - in any HELD pose: the animated pose barely changed for HOLD_DELAY_S, so
 *     clips that already move (idle breathes on its own) are left alone;
 *   - always, in states that ask for it (`breathe: true` in AnimationStates —
 *     the thinking states, whose clip is nearly still).
 *
 * How: it runs AFTER the mixer and BEFORE vrm.update, and multiplies small
 * local rotations onto spine, chest, neck and shoulders. The head is left to
 * HeadController (gaze follow), which writes that bone itself. A bone the
 * mixer did not rewrite this frame (no track in the clip) still holds last
 * frame's breathing; that offset is undone first, so it can never accumulate.
 */

/** Below this angular speed (deg/s, largest of the measured bones) the pose counts as still. */
export const STILL_DEG_PER_S = 3
/** How long the pose must stay still before breathing fades in. */
export const HOLD_DELAY_S = 0.35
const FADE_IN_PER_S = 1 / 0.6
const FADE_OUT_PER_S = 1 / 0.25

const BREATH_PERIOD_S = 4.2
const SWAY_PERIOD_S = 7.3
const TURN_PERIOD_S = 9.7

const DEG = Math.PI / 180

/** Local rotation offsets at full breath / full sway, radians. */
interface Offsets { x?: number; y?: number; z?: number }

const BREATH: Partial<Record<VRMHumanBoneName, Offsets>> = {
  [VRMHumanBoneName.Spine]: { x: 0.5 * DEG },
  [VRMHumanBoneName.Chest]: { x: 0.9 * DEG },
  [VRMHumanBoneName.UpperChest]: { x: 0.7 * DEG },
  // Counter the chest so the head stays level while the torso breathes.
  [VRMHumanBoneName.Neck]: { x: -1.2 * DEG },
  // Normalized rig: left arm along +X, right along −X, so +Z lifts the left
  // shoulder and −Z the right one.
  [VRMHumanBoneName.LeftShoulder]: { z: 1.2 * DEG },
  [VRMHumanBoneName.RightShoulder]: { z: -1.2 * DEG },
}
const SWAY: Partial<Record<VRMHumanBoneName, Offsets>> = {
  [VRMHumanBoneName.Spine]: { z: 0.5 * DEG },
  [VRMHumanBoneName.Neck]: { z: -0.4 * DEG },
}
const TURN: Partial<Record<VRMHumanBoneName, Offsets>> = {
  [VRMHumanBoneName.Chest]: { y: 0.4 * DEG },
}

/** Bones whose motion decides "is the pose held". */
const MEASURED: VRMHumanBoneName[] = [
  VRMHumanBoneName.Hips,
  VRMHumanBoneName.Spine,
  VRMHumanBoneName.Chest,
  VRMHumanBoneName.Neck,
  VRMHumanBoneName.Head,
  VRMHumanBoneName.LeftUpperArm,
  VRMHumanBoneName.RightUpperArm,
  VRMHumanBoneName.LeftLowerArm,
  VRMHumanBoneName.RightLowerArm,
]

export interface HumanoidLike {
  getNormalizedBoneNode(name: VRMHumanBoneName): THREE.Object3D | null
}

interface Slot {
  node: THREE.Object3D
  base: THREE.Quaternion
  written: THREE.Quaternion
  hasWritten: boolean
}

const euler = new THREE.Euler()
const offsetQ = new THREE.Quaternion()

export class BreathingLayer {
  private readonly offsetSlots: { slot: Slot; name: VRMHumanBoneName }[] = []
  private readonly measured: { node: THREE.Object3D; prev: THREE.Quaternion }[] = []
  private readonly slots = new Map<THREE.Object3D, Slot>()
  private stillFor = 0
  private weightValue = 0
  private time = 0
  private primed = false

  constructor(humanoid: HumanoidLike) {
    const names = new Set<VRMHumanBoneName>([
      ...(Object.keys(BREATH) as VRMHumanBoneName[]),
      ...(Object.keys(SWAY) as VRMHumanBoneName[]),
      ...(Object.keys(TURN) as VRMHumanBoneName[]),
    ])
    for (const name of names) {
      const node = humanoid.getNormalizedBoneNode(name)
      if (!node) continue
      const slot: Slot = { node, base: new THREE.Quaternion(), written: new THREE.Quaternion(), hasWritten: false }
      this.slots.set(node, slot)
      this.offsetSlots.push({ slot, name })
    }
    for (const name of MEASURED) {
      const node = humanoid.getNormalizedBoneNode(name)
      if (node) this.measured.push({ node, prev: new THREE.Quaternion() })
    }
  }

  /** Current blend weight, 0..1 (for tests and the dev panel). */
  get weight(): number {
    return this.weightValue
  }

  /**
   * Call once per frame after the animation mixer, before vrm.update.
   * @param forced breathing on regardless of motion (the state asks for it)
   */
  update(delta: number, forced: boolean): void {
    // 1. Undo our own offset where the mixer did not rewrite the bone.
    for (const slot of this.slots.values()) {
      if (slot.hasWritten && slot.node.quaternion.equals(slot.written)) slot.node.quaternion.copy(slot.base)
      slot.hasWritten = false
    }

    // 2. How fast is the animated pose (without us) moving?
    let maxDeg = 0
    for (const m of this.measured) {
      if (this.primed) maxDeg = Math.max(maxDeg, m.prev.angleTo(m.node.quaternion) / DEG)
      m.prev.copy(m.node.quaternion)
    }
    const speed = this.primed && delta > 0 ? maxDeg / delta : Infinity
    this.primed = true
    this.stillFor = speed < STILL_DEG_PER_S ? this.stillFor + delta : 0

    // 3. Ease the weight toward on/off.
    const target = forced || this.stillFor >= HOLD_DELAY_S ? 1 : 0
    const rate = target > this.weightValue ? FADE_IN_PER_S : FADE_OUT_PER_S
    const step = rate * delta
    this.weightValue = target > this.weightValue
      ? Math.min(target, this.weightValue + step)
      : Math.max(target, this.weightValue - step)

    this.time += delta
    if (this.weightValue <= 1e-3) return

    // 4. Apply.
    const breath = 0.5 - 0.5 * Math.cos((2 * Math.PI * this.time) / BREATH_PERIOD_S) // 0..1, smooth
    const sway = Math.sin((2 * Math.PI * this.time) / SWAY_PERIOD_S)
    const turn = Math.sin((2 * Math.PI * this.time) / TURN_PERIOD_S)
    const w = this.weightValue
    for (const { slot, name } of this.offsetSlots) {
      const b = BREATH[name]
      const s = SWAY[name]
      const t = TURN[name]
      const x = ((b?.x ?? 0) * breath + (s?.x ?? 0) * sway + (t?.x ?? 0) * turn) * w
      const y = ((b?.y ?? 0) * breath + (s?.y ?? 0) * sway + (t?.y ?? 0) * turn) * w
      const z = ((b?.z ?? 0) * breath + (s?.z ?? 0) * sway + (t?.z ?? 0) * turn) * w
      slot.base.copy(slot.node.quaternion)
      offsetQ.setFromEuler(euler.set(x, y, z))
      slot.node.quaternion.multiply(offsetQ)
      slot.written.copy(slot.node.quaternion)
      slot.hasWritten = true
    }
  }
}
