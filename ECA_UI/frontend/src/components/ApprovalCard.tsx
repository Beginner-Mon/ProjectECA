import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import PanelHeader from '@/components/ui/PanelHeader'
import { approvalFlags, hasAnyFlag, visibleText } from '@/lib/approvalDisplay'
import {
  canAnswer,
  isExpired,
  msUntilExpiry,
  provenanceLabels,
  type ApprovalCardData,
  type ApprovalDecision,
  type ApprovalState,
} from '@/lib/approvalCard'

interface ApprovalCardProps {
  card: ApprovalCardData
  onAnswer: (decision: ApprovalDecision) => void
}

/** setTimeout cannot hold more than this. A card lives 30 minutes, so it is never reached. */
const MAX_TIMER_MS = 2 ** 31 - 1

/**
 * The state the card shows: a card whose time ran out reads as expired even
 * before the server has been asked. The server stays the judge: an answer sent
 * in that gap is refused with 409 and the card re-reads its state.
 */
function useShownState(card: ApprovalCardData): ApprovalState {
  const { payload, state } = card
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!canAnswer(state)) return
    const left = msUntilExpiry(payload, Date.now())
    if (left === null) return
    const timer = setTimeout(() => setNow(Date.now()), Math.min(left + 50, MAX_TIMER_MS))
    return () => clearTimeout(timer)
  }, [payload, state])
  return canAnswer(state) && isExpired(payload, now) ? 'expired' : state
}

/**
 * What the assistant wants to do, for the user to allow or refuse.
 *
 * Everything from the server is printed as plain text: React escapes it, no
 * markdown or HTML is read from it, and text-direction control characters are
 * shown as visible [U+XXXX] markers instead of being obeyed. The warnings are
 * worked out here from that same text (lib/approvalDisplay.ts).
 */
export default function ApprovalCard({ card, onAnswer }: ApprovalCardProps) {
  const { t } = useTranslation()
  const { payload } = card
  const shown = useShownState(card)
  const flags = useMemo(() => approvalFlags(payload.display, payload.recipients), [payload])
  const sources = provenanceLabels(payload)
  const answerable = canAnswer(shown)
  const busy = shown === 'approving'
  const settled = shown === 'done' || shown === 'declined' || shown === 'expired' || shown === 'invalidated'

  return (
    <section
      role="group"
      aria-label={t('approval.title')}
      className="mt-3 overflow-hidden rounded-xl border border-border/40 bg-card"
    >
      <PanelHeader
        icon={<ShieldCheck className="h-4 w-4 text-muted-foreground" />}
        title={t('approval.title')}
        action={
          settled ? (
            <span className="text-xs font-medium text-muted-foreground">{t(`approval.state_${shown}`)}</span>
          ) : undefined
        }
      />
      <div className="space-y-3 px-4 py-3 text-sm">
        <p className="break-words text-xs text-muted-foreground">
          {visibleText(payload.plugin_id)} / {visibleText(payload.tool)}
        </p>

        {payload.recipients.length > 0 && (
          <div>
            <p className="text-xs text-muted-foreground">{t('approval.recipients')}</p>
            <ul className="mt-0.5 space-y-0.5">
              {payload.recipients.map((r, i) => (
                <li key={i} className="whitespace-pre-wrap break-words text-foreground">
                  {visibleText(r)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {payload.display.map((field, i) => (
          <div key={i}>
            <p className="break-words text-xs text-muted-foreground">{visibleText(field.label)}</p>
            <p className="mt-0.5 whitespace-pre-wrap break-words text-foreground">{visibleText(field.value)}</p>
          </div>
        ))}

        {hasAnyFlag(flags) && (
          <div className="space-y-1 text-xs text-foreground">
            {flags.external_domains.length > 0 && (
              <FlagLine text={t('approval.flag_domains', { domains: flags.external_domains.join(', ') })} />
            )}
            {flags.bidi && <FlagLine text={t('approval.flag_bidi')} />}
            {flags.homoglyph && <FlagLine text={t('approval.flag_homoglyph')} />}
          </div>
        )}

        {sources.length > 0 && (
          <p className="break-words text-xs text-muted-foreground">
            {t('approval.based_on', { sources: sources.map(visibleText).join(', ') })}
          </p>
        )}

        {shown === 'error' && (
          <p role="alert" className="text-xs text-destructive">
            {t('approval.error')}
          </p>
        )}

        {(answerable || busy) && (
          <>
            <div className="flex items-center gap-2">
              <Button size="sm" disabled={busy} onClick={() => onAnswer('approve')}>
                {busy && <Loader2 className="animate-spin" />}
                {t('approval.approve')}
              </Button>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => onAnswer('decline')}>
                {t('approval.decline')}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">{t('approval.note')}</p>
          </>
        )}
      </div>
    </section>
  )
}

function FlagLine({ text }: { text: string }) {
  return (
    <p className="flex items-start gap-1.5 break-words">
      <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{text}</span>
    </p>
  )
}
