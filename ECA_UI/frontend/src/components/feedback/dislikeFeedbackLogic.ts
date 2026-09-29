import { FEEDBACK_REASONS, type FeedbackReason } from '@/lib/api'

/**
 * Which reason chips this message's modal should offer.
 *
 * `motion_issue` only makes sense when the reply actually had a motion —
 * offering it otherwise is a chip that can never apply. `voice_issue` has no
 * such gate: the speaker button keeps its own clip independent of `speech`
 * (cache, or a fresh POST /tts), and a restored message never carries
 * `speech` at all even though it can still be played and criticised — so it
 * is always offered.
 *
 * Pulled out of DislikeFeedbackModal.tsx: `react-refresh/only-export-components`
 * forbids a component file from also exporting plain functions, so the logic
 * that needs testing without rendering React lives here instead.
 */
export function visibleReasons(hasMotion: boolean): FeedbackReason[] {
  return FEEDBACK_REASONS.filter((reason) => reason !== 'motion_issue' || hasMotion)
}

/**
 * Submit is allowed once the user has said something beyond the bare 👎
 * already saved when the modal opened — at least one chip, or non-blank text.
 */
export function canSubmit(reasons: FeedbackReason[], comment: string): boolean {
  return reasons.length > 0 || comment.trim().length > 0
}
