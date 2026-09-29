import { FEEDBACK_REASONS, type FeedbackReason } from '@/lib/api'

/**
 * Which reason chips this message's modal should offer.
 *
 * `motion_issue` and `voice_issue` only make sense when the reply actually
 * had a motion / a voice clip — offering them otherwise is a chip that can
 * never apply.
 *
 * Pulled out of DislikeFeedbackModal.tsx: `react-refresh/only-export-components`
 * forbids a component file from also exporting plain functions, so the logic
 * that needs testing without rendering React lives here instead.
 */
export function visibleReasons(hasMotion: boolean, hasSpeech: boolean): FeedbackReason[] {
  return FEEDBACK_REASONS.filter((reason) => {
    if (reason === 'motion_issue') return hasMotion
    if (reason === 'voice_issue') return hasSpeech
    return true
  })
}

/**
 * Submit is allowed once the user has said something beyond the bare 👎
 * already saved when the modal opened — at least one chip, or non-blank text.
 */
export function canSubmit(reasons: FeedbackReason[], comment: string): boolean {
  return reasons.length > 0 || comment.trim().length > 0
}
