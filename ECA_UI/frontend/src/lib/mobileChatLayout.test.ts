import { describe, expect, it } from 'vitest'
import { chatHeightLimit, chatRailTop, clampChatHeight, moveChatDrag } from './mobileChatLayout'

describe('mobile conversation sizing', () => {
  it('allows intermediate sizes but never exceeds 35vh or goes below zero', () => {
    const maximum = chatHeightLimit(800, 800, 150, 180)
    expect(maximum).toBe(280)
    expect(clampChatHeight(137, maximum)).toBe(137)
    expect(clampChatHeight(900, maximum)).toBe(280)
    expect(clampChatHeight(-20, maximum)).toBe(0)
  })

  it('reserves space for a keyboard, taller composer, rail and safe area', () => {
    expect(chatHeightLimit(800, 450, 180, 180, 24)).toBe(50)
    expect(chatHeightLimit(800, 300, 200, 180)).toBe(0)
    expect(chatHeightLimit(400, 400, 150, 180)).toBe(54)
  })

  it('keeps tap jitter from resizing or closing the conversation', () => {
    expect(moveChatDrag(140, 500, 495, 280, false)).toEqual({ dragging: false, height: 140 })
    expect(moveChatDrag(0, 500, 498, 280, false)).toEqual({ dragging: false, height: 0 })
  })

  it('drags from closed, stops at the bounds and does not snap intermediate sizes', () => {
    expect(moveChatDrag(0, 500, 410, 280, false)).toEqual({ dragging: true, height: 90 })
    expect(moveChatDrag(90, 500, 100, 280, true).height).toBe(280)
    expect(moveChatDrag(90, 500, 700, 280, true).height).toBe(0)
    expect(moveChatDrag(90, 500, 502, 280, true)).toEqual({ dragging: true, height: 88 })
  })

  it('continues from the visible height when grabbing an animation midway', () => {
    expect(moveChatDrag(123, 500, 480, 280, false).height).toBe(143)
  })
})

describe('right-side mobile rail', () => {
  it('starts centered and moves up only as far as needed to clear the dock', () => {
    expect(chatRailTop(0, 800, 180, null)).toBe(310)
    expect(chatRailTop(0, 800, 180, 650)).toBe(310)
    expect(chatRailTop(0, 800, 180, 430)).toBe(242)
  })

  it('returns progressively to its initial position during closing', () => {
    const positions = [430, 460, 490, 520, 650].map(top => chatRailTop(0, 800, 180, top))
    expect(positions).toEqual([242, 272, 302, 310, 310])
  })

  it('uses visual viewport offsets and protects the top safe area', () => {
    expect(chatRailTop(120, 450, 180, null)).toBe(255)
    expect(chatRailTop(120, 450, 180, 350)).toBe(162)
    expect(chatRailTop(120, 450, 180, 200, 24)).toBe(152)
  })

  it('keeps both rail and maximum-height chat apart across mobile sizes', () => {
    for (const visibleHeight of [320, 375, 667, 844, 932]) {
      const baseHeight = 120
      const railHeight = 160
      const topInset = 20
      const maximum = chatHeightLimit(visibleHeight, visibleHeight, baseHeight, railHeight, topInset)
      const dockTop = visibleHeight - baseHeight - maximum
      const railTop = chatRailTop(0, visibleHeight, railHeight, dockTop, topInset)
      expect(maximum).toBeLessThanOrEqual(visibleHeight * 0.35)
      expect(railTop).toBeGreaterThanOrEqual(topInset + 8)
      expect(railTop + railHeight + 8).toBeLessThanOrEqual(dockTop)
    }
  })
})
