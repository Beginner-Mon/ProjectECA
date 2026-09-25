import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Activity, Loader2 } from 'lucide-react'
import { useMotion } from '../hooks/useMotion'
import { fetchMotionStatus } from '../lib/api'

/**
 * MobileMotionChips — horizontal scroll replay chips for the mobile chat
 * top-strip (left side). Same replay source as MotionControlPanel's
 * dropdown: `sessionMotions` newest-first, clip cached under job_id.
 *
 * Renders nothing when there is nothing to replay, so the strip's left
 * side collapses instead of leaving an empty slot.
 */
export default function MobileMotionChips() {
  const { t } = useTranslation()
  const { sessionMotions, playMotionFile } = useMotion()
  const [replayingId, setReplayingId] = useState<string | null>(null)

  if (sessionMotions.length === 0) return null

  const replay = (jobId: string) => {
    const picked = sessionMotions.find((m) => m.jobId === jobId)
    if (!picked || replayingId) return
    setReplayingId(jobId)
    void (async () => {
      try {
        // Same fallback as MotionControlPanel: a motion restored from history
        // has no URL and its signature expires after 5 minutes — resolve a
        // fresh signed URL at tap time.
        let url = picked.url
        if (!url) {
          const status = await fetchMotionStatus(picked.jobId)
          if (status.status !== 'done' || !status.url) {
            console.warn('[motion] replay unavailable:', status)
            return
          }
          url = status.url
        }
        await playMotionFile(url, picked.jobId, picked.label)
      } finally {
        setReplayingId(null)
      }
    })()
  }

  return (
    <div
      className="flex items-center gap-1.5 overflow-x-auto flex-1 min-w-0 px-3 py-2 border-b border-border/40 shrink-0"
      aria-label={t('motion.replay_motion')}
    >
      {sessionMotions.map((m) => {
        const busy = replayingId === m.jobId
        return (
          <button
            key={m.jobId}
            onClick={() => replay(m.jobId)}
            disabled={busy}
            title={m.label}
            className="flex items-center gap-1 shrink-0 max-w-[140px] px-2 py-1 rounded-full text-[11px] border border-primary/30 bg-primary/10 text-foreground hover:bg-primary/20 transition-colors disabled:opacity-60"
          >
            {busy ? (
              <Loader2 className="w-3 h-3 animate-spin shrink-0" />
            ) : (
              <Activity className="w-3 h-3 shrink-0 text-primary" />
            )}
            <span className="truncate">{m.label}</span>
          </button>
        )
      })}
    </div>
  )
}
