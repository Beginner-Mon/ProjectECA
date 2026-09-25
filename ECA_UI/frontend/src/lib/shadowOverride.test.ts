import { describe, expect, it } from 'vitest'
import * as THREE from 'three'
import { applySoftShadows } from './shadowOverride'

function mtoon(shift: number, shadeHex: string) {
  return {
    isMToonMaterial: true,
    shadingShiftFactor: shift,
    shadeColorFactor: new THREE.Color(shadeHex),
    userData: {} as Record<string, unknown>,
  }
}

describe('applySoftShadows — the "Softer shadows" graphics toggle', () => {
  it('on: applies the shading shift and keeps the character\'s own shade colour', () => {
    const m = mtoon(0.1, '#aa7766')
    applySoftShadows(m, true, { shadingShiftFactor: 0.85, shadeColorHex: null })
    expect(m.shadingShiftFactor).toBe(0.85)
    expect(m.shadeColorFactor.getHexString()).toBe('aa7766')
  })

  it('on with a shade colour configured: applies that too', () => {
    const m = mtoon(0.1, '#aa7766')
    applySoftShadows(m, true, { shadingShiftFactor: 0.85, shadeColorHex: '#1a1020' })
    expect(m.shadeColorFactor.getHexString()).toBe('1a1020')
  })

  it('off after on: restores exactly what the model had (the reported bug)', () => {
    const m = mtoon(0.1, '#aa7766')
    applySoftShadows(m, true, { shadingShiftFactor: 0.85, shadeColorHex: '#1a1020' })
    applySoftShadows(m, false, { shadingShiftFactor: 0.85, shadeColorHex: '#1a1020' })
    expect(m.shadingShiftFactor).toBe(0.1)
    expect(m.shadeColorFactor.getHexString()).toBe('aa7766')
  })

  it('on twice: remembers the ORIGINAL, not the overridden values', () => {
    const m = mtoon(0.1, '#aa7766')
    const cfg = { shadingShiftFactor: 0.85, shadeColorHex: '#1a1020' }
    applySoftShadows(m, true, cfg)
    applySoftShadows(m, true, cfg)
    applySoftShadows(m, false, cfg)
    expect(m.shadingShiftFactor).toBe(0.1)
    expect(m.shadeColorFactor.getHexString()).toBe('aa7766')
  })

  it('off on a material that was never overridden: leaves it alone', () => {
    const m = mtoon(0.3, '#445566')
    applySoftShadows(m, false, { shadingShiftFactor: 0.85, shadeColorHex: null })
    expect(m.shadingShiftFactor).toBe(0.3)
    expect(m.shadeColorFactor.getHexString()).toBe('445566')
  })

  it('ignores materials that are not MToon', () => {
    const plain = { isMToonMaterial: false, shadingShiftFactor: 0.2, shadeColorFactor: new THREE.Color('#123456'), userData: {} }
    expect(applySoftShadows(plain, true, { shadingShiftFactor: 0.85, shadeColorHex: '#1a1020' })).toBe(false)
    expect(plain.shadingShiftFactor).toBe(0.2)
  })
})
