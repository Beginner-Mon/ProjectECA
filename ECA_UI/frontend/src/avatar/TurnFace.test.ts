import { describe, expect, it } from 'vitest'
import { TurnFace } from './TurnFace'
import { facialOf } from '../lib/AnimationStates'

const concern = { name: 'sad' as const, intensity: 0.3 }
const smile = { name: 'happy' as const, intensity: 0.6 }

describe('TurnFace', () => {
  it('thinking holds neutral until the planner reads the message', () => {
    const f = new TurnFace()
    f.beginTurn()
    expect(f.enterState(facialOf('thinking_intro'))).toEqual({ name: 'neutral', intensity: 1 })
    expect(f.holding).toBe(true)
    expect(f.setListening(concern, false)).toEqual(concern)
  })

  it('the listening face carries into the ponder loop', () => {
    const f = new TurnFace()
    f.beginTurn()
    f.enterState(facialOf('thinking_intro'))
    f.setListening(concern, false)
    expect(f.enterState(facialOf('thinking_loop'))).toEqual(concern)
  })

  it('a safety turn stays neutral whatever the affect', () => {
    const f = new TurnFace()
    f.beginTurn()
    f.enterState(facialOf('thinking_intro'))
    expect(f.setListening(concern, true)).toEqual({ name: 'neutral', intensity: 1 })
    expect(f.restingFace()).toEqual({ name: 'neutral', intensity: 1 })
  })

  it('a reply emotion that arrives during the loop survives the outro', () => {
    const f = new TurnFace()
    f.beginTurn()
    f.enterState(facialOf('thinking_intro'))
    f.enterState(facialOf('thinking_loop'))
    f.setReply(smile)
    expect(f.enterState(facialOf('thinking_outro'))).toBeNull()
    expect(f.holding).toBe(false)
    expect(f.enterState(facialOf('idle'))).toBeNull()
  })

  it('a reply that lands during the intro is not wiped by the loop', () => {
    const f = new TurnFace()
    f.beginTurn()
    f.enterState(facialOf('thinking_intro'))
    f.setReply(smile)
    expect(f.enterState(facialOf('thinking_loop'))).toBeNull()
  })

  it('exercise smiles lightly, then gives the reply its face back', () => {
    const f = new TurnFace()
    f.beginTurn()
    f.setReply(smile)
    expect(f.enterState(facialOf('exercise'))).toEqual({ name: 'happy', intensity: 0.35 })
    expect(f.holding).toBe(true)
    // A listening face cannot replace an action's face.
    expect(f.setListening(concern, false)).toBeNull()
    expect(f.enterState(facialOf('idle'))).toEqual(smile)
  })

  it('after exercise with no reply, the face rests warm', () => {
    const f = new TurnFace()
    f.enterState(facialOf('exercise'))
    expect(f.enterState(facialOf('idle'))).toEqual({ name: 'relaxed', intensity: 0.2 })
  })

  it('a new turn forgets the last one', () => {
    const f = new TurnFace()
    f.beginTurn()
    f.setListening(concern, true)
    f.setReply(smile)
    f.beginTurn()
    expect(f.restingFace()).toEqual({ name: 'relaxed', intensity: 0.2 })
    expect(f.enterState(facialOf('thinking_intro'))).toEqual({ name: 'neutral', intensity: 1 })
  })

  it('every hold in the state table stays at or under 0.5, except neutral', () => {
    for (const state of ['idle', 'greeting', 'bored', 'thinking_intro', 'thinking_loop', 'thinking_outro', 'exercise', 'gesture'] as const) {
      const hold = facialOf(state).hold
      if (hold && hold.name !== 'neutral') expect(hold.intensity).toBeLessThanOrEqual(0.5)
    }
  })
})
