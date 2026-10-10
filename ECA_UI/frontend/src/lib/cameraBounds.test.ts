import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { PAN_RADIUS, PAN_Z_MAX, PAN_Z_MIN, keepInsideSphere, panCorrection } from './cameraBounds'

const v = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z)

describe('panCorrection — the orbit point stays near the character', () => {
  const character = v(0, 1.5, 0.9)

  it('leaves an orbit point inside the bounds alone', () => {
    expect(panCorrection(v(1, 2, 1.2), character, new THREE.Vector3()).lengthSq()).toBe(0)
  })

  it('pulls a far pan back to the edge of the circle, in the same direction', () => {
    const target = v(10, 1.5, 1)
    const fix = panCorrection(target, character, new THREE.Vector3())
    target.add(fix)
    expect(Math.hypot(target.x - character.x, target.y - character.y)).toBeCloseTo(PAN_RADIUS)
    expect(target.x).toBeGreaterThan(0)
    expect(target.z).toBe(1) // height untouched
  })

  it('keeps the orbit point between the floor and a little above the head', () => {
    const low = v(0, 1.5, -3)
    low.add(panCorrection(low, character, new THREE.Vector3()))
    expect(low.z).toBe(PAN_Z_MIN)
    const high = v(0, 1.5, 9)
    high.add(panCorrection(high, character, new THREE.Vector3()))
    expect(high.z).toBe(PAN_Z_MAX)
  })

  it('follows the character, not the stage origin', () => {
    const walked = v(20, 1.5, 0.9)
    expect(panCorrection(v(21, 1.5, 1), walked, new THREE.Vector3()).lengthSq()).toBe(0)
  })
})

describe('keepInsideSphere — the camera never leaves the dome', () => {
  const centre = v(0, 1.5, 0)

  it('does nothing inside', () => {
    const p = v(3, 1.5, 2)
    expect(keepInsideSphere(p, centre, 29)).toBe(false)
    expect(p.x).toBe(3)
  })

  it('pulls an escaped camera back onto the limit along the same line', () => {
    const p = v(40, 1.5, 0)
    expect(keepInsideSphere(p, centre, 29)).toBe(true)
    expect(p.distanceTo(centre)).toBeCloseTo(29)
    expect(p.x).toBeGreaterThan(0)
  })
})
