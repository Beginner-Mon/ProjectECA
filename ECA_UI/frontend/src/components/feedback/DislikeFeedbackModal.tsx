import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import type { FeedbackReason } from '@/lib/api'
import { canSubmit, visibleReasons } from './dislikeFeedbackLogic'

const MAX_COMMENT_LENGTH = 1000

interface DislikeFeedbackModalProps {
  messageHasMotion: boolean
  onSubmit: (reasons: FeedbackReason[], comment: string) => Promise<void>
  onCancel: () => void
}

/**
 * The 👎 reason box — centered on screen on desktop and mobile alike.
 *
 * Portals to `document.body`: the chat panel sits inside a floating panel
 * with a CSS transform (floating-ui), and `fixed inset-0` inside that
 * ancestor would be clipped to the panel instead of covering the viewport.
 * Same reasoning as `ui/confirm-dialog.tsx`. The portaled root carries
 * `data-dialog-layer` so FloatingNavBar's outside-click handler recognises a
 * press inside it as belonging to the panel that opened it, not a click that
 * should close that panel.
 *
 * The vote itself (rating=-1) is being saved as this opens (the caller fires
 * it without waiting) — Cancel only discards the reason/comment, it never
 * touches the vote.
 *
 * The caller renders this component only while the modal is open (no `open`
 * prop here), so a fresh mount always starts with empty state — nothing to
 * re-seed from props.
 */
export default function DislikeFeedbackModal({
  messageHasMotion,
  onSubmit,
  onCancel,
}: DislikeFeedbackModalProps) {
  const { t } = useTranslation()
  const [reason, setReason] = useState<FeedbackReason | ''>('')
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Esc, the overlay and X all route through this. While a submit is in
  // flight none of them may close the modal: that would hide a later error
  // and throw away reasons/comment the user already entered.
  const cancel = useCallback(() => {
    if (submitting) return
    onCancel()
  }, [submitting, onCancel])

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') cancel()
    },
    [cancel],
  )

  useEffect(() => {
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])

  const handleSubmit = async () => {
    if (submitting || !canSubmit(reason, comment)) return
    setSubmitting(true)
    setError(null)
    try {
      // The API still takes an array; the dropdown makes it at most one.
      await onSubmit(reason ? [reason] : [], comment.trim())
      // On success the caller stops rendering this component; nothing to
      // reset here.
    } catch {
      setError(t('feedback.error_save'))
      setSubmitting(false)
    }
  }

  const options = visibleReasons(messageHasMotion).map((code) => ({
    code,
    label: t(`feedback.reasons.${code}`),
  }))

  const node = (
    <div data-dialog-layer className="fixed inset-0 z-[10001] flex items-center justify-center p-4">
      {/* overlay — clicking it cancels, same as X and Esc (except mid-submit) */}
      <div className="absolute inset-0 bg-black/50 backdrop-blur-sm" onClick={cancel} />
      <div className="relative w-full max-w-md rounded-2xl bg-card border border-border/50 shadow-[0_16px_64px_rgba(0,0,0,0.4)] flex flex-col overflow-hidden animate-panel-in max-h-[85vh]">
        <div className="flex items-start gap-2 px-4 py-3 border-b border-border/40 shrink-0">
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-foreground">{t('feedback.modal_title')}</h3>
            <p className="text-xs text-muted-foreground mt-0.5">{t('feedback.modal_subtitle')}</p>
          </div>
          <button
            onClick={cancel}
            disabled={submitting}
            className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors shrink-0 disabled:opacity-50"
            aria-label={t('common.close')}
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 space-y-4 overflow-y-auto">
          {error && (
            <div className="text-sm text-destructive bg-destructive/10 py-2 px-3 rounded-lg">{error}</div>
          )}

          <div>
            <label className="text-xs text-muted-foreground mb-1.5 block" htmlFor="feedback-reason">
              {t('feedback.reason_label')}
            </label>
            <select
              id="feedback-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value as FeedbackReason | '')}
              className="w-full rounded-lg border border-border/50 bg-background px-3 py-2 text-sm text-foreground cursor-pointer focus:outline-none focus:ring-2 focus:ring-primary/40"
            >
              <option value="" disabled className="bg-card text-muted-foreground">
                {t('feedback.reason_placeholder')}
              </option>
              {options.map(({ code, label }) => (
                <option key={code} value={code} className="bg-card text-foreground">
                  {label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-muted-foreground mb-1.5 block" htmlFor="feedback-comment">
              {t('feedback.comment_label')}
            </label>
            <textarea
              id="feedback-comment"
              value={comment}
              onChange={(e) => setComment(e.target.value.slice(0, MAX_COMMENT_LENGTH))}
              maxLength={MAX_COMMENT_LENGTH}
              placeholder={t('feedback.comment_placeholder')}
              rows={5}
              className="w-full rounded-lg border border-border/50 bg-background px-3 py-2 text-sm text-foreground resize-none focus:outline-none focus:ring-2 focus:ring-primary/40 min-h-[7.5rem]"
            />
            <p className="text-[0.7rem] text-muted-foreground mt-1 text-right">
              {t('feedback.char_count', { count: comment.length, max: MAX_COMMENT_LENGTH })}
            </p>
          </div>
        </div>

        <div className="flex justify-end gap-2 px-4 py-3 border-t border-border/40 shrink-0">
          <button
            onClick={cancel}
            disabled={submitting}
            className="h-8 px-4 rounded-lg text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-secondary/60 transition-colors disabled:opacity-50"
          >
            {t('feedback.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !canSubmit(reason, comment)}
            className="h-8 px-4 rounded-lg text-xs font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? t('feedback.submitting') : t('feedback.submit')}
          </button>
        </div>
      </div>
    </div>
  )

  return createPortal(node, document.body)
}
