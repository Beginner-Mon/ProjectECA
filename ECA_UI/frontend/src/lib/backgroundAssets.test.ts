import { describe, expect, it } from 'vitest'
import { backdropMode, backdropOwnsFloor, coverTransform } from './backgroundAssets'

describe('coverTransform — backdrop fills the view without stretching', () => {
  it('shows the whole image when the view has the same shape', () => {
    expect(coverTransform(2, 2)).toEqual({ repeat: [1, 1], offset: [0, 0] })
  })

  it('crops top and bottom equally on a view wider than the image', () => {
    const { repeat, offset } = coverTransform(4, 2) // ultrawide screen, 2:1 image
    expect(repeat[0]).toBe(1)
    expect(repeat[1]).toBeCloseTo(0.5)
    expect(offset[1]).toBeCloseTo(0.25) // centred: 25% cut from each end
  })

  it('crops the sides equally on a phone in portrait', () => {
    const { repeat, offset } = coverTransform(9 / 19.5, 2.11)
    expect(repeat[1]).toBe(1)
    expect(repeat[0]).toBeCloseTo(9 / 19.5 / 2.11)
    expect(offset[0]).toBeCloseTo((1 - repeat[0]) / 2)
  })

  it('keeps the image proportions: visible region has the view aspect', () => {
    const view = 16 / 9
    const image = 2.11
    const { repeat } = coverTransform(view, image)
    // visible width/height in image pixels, relative: (repeat.x * image) / repeat.y
    expect((repeat[0] * image) / repeat[1]).toBeCloseTo(view)
  })

  it('falls back to the whole image for a degenerate size (canvas not measured yet)', () => {
    expect(coverTransform(0, 2)).toEqual({ repeat: [1, 1], offset: [0, 0] })
    expect(coverTransform(Number.NaN, 2)).toEqual({ repeat: [1, 1], offset: [0, 0] })
  })
})

describe('backdropMode / backdropOwnsFloor — which backdrop is on, and who draws the floor', () => {
  const base = { kind: 'flat' as const, ground: null }

  it('dome needs no image', () => {
    expect(backdropMode({ ...base, kind: 'dome' }, null)).toBe('dome')
  })

  it('image kinds need a resolved image, else fall back to the gradient', () => {
    expect(backdropMode({ ...base, kind: 'flat' }, 'x.jpg')).toBe('image')
    expect(backdropMode({ ...base, kind: 'flat' }, null)).toBe('gradient')
    expect(backdropMode({ ...base, kind: 'panorama' }, null)).toBe('gradient')
  })

  it('the dome and a grounded panorama bring their own floor; others do not', () => {
    expect(backdropOwnsFloor({ ...base, kind: 'dome' }, null)).toBe(true)
    expect(backdropOwnsFloor({ kind: 'panorama', ground: { height: 1.6, radius: 10, yawDeg: 0 } }, 'p.jpg')).toBe(true)
    expect(backdropOwnsFloor({ kind: 'panorama', ground: null }, 'p.jpg')).toBe(false)
    expect(backdropOwnsFloor({ ...base, kind: 'flat' }, 'x.jpg')).toBe(false)
    expect(backdropOwnsFloor({ ...base, kind: 'panorama', ground: { height: 1.6, radius: 10, yawDeg: 0 } }, null)).toBe(false)
  })
})
