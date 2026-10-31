import type { CanonicalEmotion } from './AvatarProfile'
import type { FacialPolicy } from '../lib/AnimationStates'

export interface Face {
  name: CanonicalEmotion
  intensity: number
}

const NEUTRAL: Face = { name: 'neutral', intensity: 1 }
/** Where the face settles once a turn is over: warm, not blank. */
const RESTING: Face = { name: 'relaxed', intensity: 0.2 }

/**
 * Which face a conversation turn should show, given what the body is doing
 * (docs/plans/avatar-emotion-context.md). Pure rules, no VRM: AvatarController
 * applies whatever this returns.
 *
 * Three sources, in rising priority:
 * 1. the body state's `hold` (thinking → neutral, exercise → light smile);
 * 2. the listening face — the planner's read of the user's message, shown
 *    while the avatar thinks;
 * 3. the reply's emotion tag.
 * An `action` hold (exercise) is the exception: it covers 2 and 3 while it
 * plays and hands the turn's face back when it ends.
 */
export class TurnFace {
  private reply: Face | null = null
  private listening: Face | null = null
  private safety = false
  private policy: FacialPolicy = { wander: true }

  /** A hold is in force: the avatar counts as engaged, so neither the idle
   *  wanderer nor the engaged → idle fade can change the face under it. */
  get holding(): boolean {
    return this.policy.hold !== undefined
  }

  /** The user sent a message: forget the previous turn's faces. */
  beginTurn(): void {
    this.reply = null
    this.listening = null
    this.safety = false
  }

  /**
   * The body entered a state. Returns the face to show now, or null to keep
   * the current one.
   */
  enterState(policy: FacialPolicy): Face | null {
    const leavingAction = this.policy.action === true && policy.action !== true
    this.policy = policy
    const hold = policy.hold
    if (hold) {
      if (policy.action) return hold
      // A yielding hold: the turn's face wins once there is one.
      if (this.reply) return null
      return this.listening ?? hold
    }
    if (leavingAction) return this.reply ?? this.restingFace()
    return null
  }

  /**
   * The planner read the user's message. Safety turns (red flag, referral)
   * stay neutral whatever the affect. Returns the face to show now, or null
   * when something else owns the face.
   */
  setListening(face: Face | null, safety: boolean): Face | null {
    this.safety = safety
    this.listening = safety ? NEUTRAL : face
    if (!this.listening || this.reply) return null
    // Only a yielding hold (thinking) is replaced; an action keeps its face.
    if (this.policy.hold && !this.policy.action) return this.listening
    return null
  }

  /** The reply's emotion arrived (already applied by the caller). */
  setReply(face: Face): void {
    this.reply = face
  }

  /** The face once the turn is over. Safety content stays neutral. */
  restingFace(): Face {
    return this.safety ? NEUTRAL : RESTING
  }
}
