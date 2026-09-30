import { FEEDBACK_REASONS, type FeedbackReason } from '@/lib/api'

/**
 * Which reasons this message's dropdown should offer.
 *
 * `motion_issue` only makes sense when the reply actually had a motion —
 * offering it otherwise is an option that can never apply. `voice_issue` has no
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
 * already saved when the modal opened — a reason chosen, or non-blank text.
 * `reason` is '' while the dropdown still shows its placeholder.
 */
export function canSubmit(reason: FeedbackReason | '', comment: string): boolean {
  return reason !== '' || comment.trim().length > 0
}
