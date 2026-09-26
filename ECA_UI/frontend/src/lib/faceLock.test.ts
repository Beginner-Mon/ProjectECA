import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { clearanceBackoff, keepAway, limitAimOffset, noseFocus, partnerEyes, HEAD_TO_NOSE, NOSE_BELOW_EYES, PARTNER_EYE_LIFT, PARTNER_MIN_DISTANCE } from './faceLock'

const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z)

describe('noseFocus — where the locked camera aims (Z-up world)', () => {
  it('uses the eyes when the model has eye bones: just below their midpoint', () => {
    const out = noseFocus(v(0, 0, 1.45), v(-0.03, 0.02, 1.54), v(0.03, 0.02, 1.54), new THREE.Vector3())
    expect(out.x).toBeCloseTo(0)
    expect(out.y).toBeCloseTo(0.02)
    expect(out.z).toBeCloseTo(1.54 - NOSE_BELOW_EYES)
  })

  it('falls back to a lift above the head bone when there are no eye bones', () => {
    const out = noseFocus(v(0, 1.5, 1.45), null, null, new THREE.Vector3())
    expect(out.z).toBeCloseTo(1.45 + HEAD_TO_NOSE)
  })

  it('is above the head bone either way — the old lock aimed at the chin', () => {
    const head = v(0, 0, 1.45)
    expect(noseFocus(head, null, null, new THREE.Vector3()).z).toBeGreaterThan(head.z)
  })
})

describe('clearanceBackoff — the camera never ends up inside the avatar', () => {
  const nose = v(0, 0, 1.5)
  const forward = v(0, 1, 0) // camera sits in front of the face along +Y

  it('needs no back-off when every joint is far enough away', () => {
    const joints = [v(0, 0, 1.5), v(0.2, 0.1, 1.2)]
    expect(clearanceBackoff(nose, forward, 0.6, joints, 0.2)).toBe(0)
  })

  it('backs off just enough when a hand comes toward the lens', () => {
    const hand = v(0, 0.5, 1.5) // 10 cm from a camera at 0.6
    const extra = clearanceBackoff(nose, forward, 0.6, [hand], 0.2)
    expect(extra).toBeGreaterThan(0.09)
    expect(extra).toBeLessThan(0.15)
    const cam = nose.clone().addScaledVector(forward, 0.6 + extra)
    expect(cam.distanceTo(hand)).toBeGreaterThanOrEqual(0.2 - 1e-6)
  })

  it('handles several joints at once (fingers of a hand)', () => {
    const fingers = [v(0, 0.52, 1.5), v(0.02, 0.55, 1.51), v(-0.02, 0.57, 1.49)]
    const extra = clearanceBackoff(nose, forward, 0.6, fingers, 0.2)
    const cam = nose.clone().addScaledVector(forward, 0.6 + extra)
    for (const f of fingers) expect(cam.distanceTo(f)).toBeGreaterThanOrEqual(0.2 - 1e-6)
  })

  it('is bounded — a joint sitting on the view axis cannot push the camera away forever', () => {
    const onAxis = v(0, 0.6, 1.5)
    expect(clearanceBackoff(nose, forward, 0.6, [onAxis], 0.2)).toBeLessThanOrEqual(1)
  })
})

describe('limitAimOffset — pan toward the hand, but keep the nose in frame', () => {
  const cam = v(0, 0.6, 1.5)
  const nose = v(0, 0, 1.5)

  it('leaves an aim that is already close enough to the nose alone', () => {
    const aim = v(0.02, 0, 1.48)
    const out = limitAimOffset(cam, nose, aim, THREE.MathUtils.degToRad(15), new THREE.Vector3())
    expect(out.distanceTo(aim)).toBeLessThan(1e-9)
  })

  it('pulls a far-off aim back to the limit, in the same direction', () => {
    const aim = v(0, 0.3, 1.0) // the hand, well below and in front of the face
    const max = THREE.MathUtils.degToRad(15)
    const out = limitAimOffset(cam, nose, aim, max, new THREE.Vector3())
    const angle = out.clone().sub(cam).angleTo(nose.clone().sub(cam))
    expect(angle).toBeLessThanOrEqual(max + 1e-3)
    expect(angle).toBeGreaterThan(max * 0.9) // still leans toward the hand
    expect(out.z).toBeLessThan(nose.z)
  })
})

describe('clearanceBackoff — per-joint clearance (fingertips sit at the mesh surface)', () => {
  const nose = v(0, 0, 1.5)
  const forward = v(0, 1, 0)

  it('lets the camera come closer to a fingertip than to the head or body', () => {
    const fingertip = v(0, 0.45, 1.5) // 15 cm from a camera at 0.6
    expect(clearanceBackoff(nose, forward, 0.6, [fingertip], [0.12])).toBe(0)
    expect(clearanceBackoff(nose, forward, 0.6, [fingertip], [0.22])).toBeGreaterThan(0)
  })

  it('each joint keeps its own clearance', () => {
    const joints = [v(0, 0.45, 1.5), v(0.05, 0.5, 1.45)]
    const clear = [0.12, 0.22]
    const extra = clearanceBackoff(nose, forward, 0.6, joints, clear)
    const cam = nose.clone().addScaledVector(forward, 0.6 + extra)
    joints.forEach((j, i) => expect(cam.distanceTo(j)).toBeGreaterThanOrEqual(clear[i] - 1e-6))
  })
})

describe('partnerEyes — where the camera stands for the kiss (the invisible partner)', () => {
  const nose = v(0, 0, 1.55)

  const front = v(0, 1, 0) // the face lock looks at her from +Y

  it('is straight in front of her face, a little higher — no sideways drift toward the palm', () => {
    const out = partnerEyes(nose, front, new THREE.Vector3())
    expect(out.x).toBeCloseTo(0)
    expect(out.y).toBeCloseTo(PARTNER_MIN_DISTANCE)
    expect(out.z).toBeCloseTo(1.55 + PARTNER_EYE_LIFT)
  })

  it('stays outside her head (the old 13 cm midpoint was inside the skull)', () => {
    const out = partnerEyes(nose, front, new THREE.Vector3())
    expect(out.distanceTo(nose)).toBeGreaterThanOrEqual(0.25)
  })

  it('follows the lock axis when the avatar is turned', () => {
    const turned = v(1, 1, 0).normalize()
    const out = partnerEyes(nose, turned, new THREE.Vector3())
    expect(out.x).toBeCloseTo(out.y)
  })

  it('keeps the resting palm (measured on Kiss.fbx) out of the 60° view — the hand never covers the lens', () => {
    const palm = v(0.22, 0.14, 1.47)
    const cam = partnerEyes(nose, front, new THREE.Vector3())
    const view = nose.clone().sub(cam)
    const angle = THREE.MathUtils.radToDeg(palm.clone().sub(cam).angleTo(view))
    expect(angle).toBeGreaterThan(45) // outside the 16:9 horizontal half-FOV
  })
})

describe('keepAway — nothing crosses the lens', () => {
  it('pushes the camera back along the line from the face when too close', () => {
    const face = v(0, 0, 1.5)
    const cam = v(0, 0.05, 1.5)
    keepAway(cam, face, 0.09)
    expect(cam.distanceTo(face)).toBeCloseTo(0.09)
    expect(cam.y).toBeGreaterThan(0)
  })

  it('leaves a camera that is far enough alone', () => {
    const cam = v(0, 0.2, 1.5)
    keepAway(cam, v(0, 0, 1.5), 0.09)
    expect(cam.y).toBe(0.2)
  })
})
