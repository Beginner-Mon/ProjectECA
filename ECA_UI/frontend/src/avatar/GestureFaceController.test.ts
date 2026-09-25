import { describe, expect, it } from 'vitest'
import type { AvatarProfile, FaceKey } from './AvatarProfile'
import { defaultProfile } from './profiles/default'
import { bronyaProfile } from './profiles/bronya'
import { GestureFaceController, resolveFaceKey, faceTrackChannels } from './GestureFaceController'

const TRACK: FaceKey[] = [
  { t: 0, face: {} },
  { t: 1, face: { U: 1, blink: 1 } },
  { t: 2, face: { happy: 0.5 } },
]

function run(ctrl: GestureFaceController, seconds: number, step = 1 / 60) {
  for (let t = 0; t < seconds - 1e-9; t += step) ctrl.tick(Math.min(step, seconds - t))
}

describe('resolveFaceKey — profile vocabulary to this model\'s channels', () => {
  it('maps emotions through recipes, visemes and blink through the profile', () => {
    const m = resolveFaceKey(defaultProfile, { happy: 0.5, U: 0.8, blink: 1 })
    expect(Object.fromEntries(m)).toEqual({ happy: 0.5, ou: 0.8, blink: 1 })
  })

  it('uses the model\'s own names (Bronya: custom Surprised channel)', () => {
    const m = resolveFaceKey(bronyaProfile, { surprised: 1 })
    expect(Object.fromEntries(m)).toEqual({ Surprised: 1 })
  })

  it('lists every channel a profile\'s face tracks can drive (adapter must manage them)', () => {
    const profile: AvatarProfile = {
      ...defaultProfile,
      gestures: { kiss: { source: { builtIn: 'kiss' }, face: TRACK } },
    }
    expect([...faceTrackChannels(profile)].sort()).toEqual(['blink', 'happy', 'ou'])
  })
})

describe('GestureFaceController — the face that plays with a gesture', () => {
  it('does nothing until played', () => {
    const c = new GestureFaceController(defaultProfile)
    const frame = new Map([['aa', 0.7]])
    c.contribute(frame)
    expect(frame.get('aa')).toBe(0.7)
    expect(c.isPlaying).toBe(false)
  })

  it('reaches the keyframe values mid-track', () => {
    const c = new GestureFaceController(defaultProfile)
    c.play(TRACK)
    run(c, 1)
    const frame = new Map<string, number>()
    c.contribute(frame)
    expect(frame.get('ou')).toBeCloseTo(1, 2)
    expect(frame.get('blink')).toBeCloseTo(1, 2)
  })

  it('overrides lip-sync and auto-blink while it plays (the kiss owns mouth and eyes)', () => {
    const c = new GestureFaceController(defaultProfile)
    c.play(TRACK)
    run(c, 1.5)
    // What lip-sync and blink wrote this frame, before the face track runs last.
    const frame = new Map([['aa', 0.8], ['blink', 0.9]])
    c.contribute(frame)
    expect(frame.get('aa')).toBeCloseTo(0, 5) // jaw shut for the pucker
  })

  it('leaves channels it does not own alone (e.g. an emotion it never uses)', () => {
    const c = new GestureFaceController(defaultProfile)
    c.play(TRACK)
    run(c, 1)
    const frame = new Map([['angry', 0.4]])
    c.contribute(frame)
    expect(frame.get('angry')).toBe(0.4)
  })

  it('fades in from what the face already showed — no snap on the first frame', () => {
    const c = new GestureFaceController(defaultProfile)
    c.play(TRACK)
    c.tick(1 / 60)
    const frame = new Map([['happy', 1]])
    c.contribute(frame)
    expect(frame.get('happy')!).toBeGreaterThan(0.85)
  })

  it('ends by itself at the last keyframe and hands the face back', () => {
    const c = new GestureFaceController(defaultProfile)
    c.play(TRACK)
    run(c, 2.05)
    expect(c.isPlaying).toBe(false)
    const frame = new Map([['aa', 0.6]])
    c.contribute(frame)
    expect(frame.get('aa')).toBe(0.6)
  })

  it('stop() fades out quickly instead of cutting (gesture interrupted)', () => {
    const c = new GestureFaceController(defaultProfile)
    c.play(TRACK)
    run(c, 1)
    c.stop()
    expect(c.isPlaying).toBe(true) // still fading
    run(c, 0.5)
    expect(c.isPlaying).toBe(false)
  })

  it('an empty or one-key track is ignored rather than dividing by zero', () => {
    const c = new GestureFaceController(defaultProfile)
    c.play([])
    expect(c.isPlaying).toBe(false)
    c.play([{ t: 0, face: { happy: 1 } }])
    expect(c.isPlaying).toBe(false)
  })
})
