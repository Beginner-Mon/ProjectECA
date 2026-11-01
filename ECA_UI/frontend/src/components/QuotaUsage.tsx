import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getMyQuota, type QuotaStatus } from '../lib/api'
import { formatTokens, usageFraction } from '../lib/quota'

/**
 * Today's token usage against the user's tier (api/routes_quota.py GET
 * /me/quota). Read once when the profile opens — it is a snapshot, not a meter.
 */
export default function QuotaUsage() {
  const { t, i18n } = useTranslation()
  const [status, setStatus] = useState<QuotaStatus | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    getMyQuota()
      .then((s) => { if (alive) setStatus(s) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [])

  if (failed) return <p className="text-sm text-muted-foreground">{t('profile.usage_unavailable')}</p>
  if (!status) return <p className="text-sm text-muted-foreground">{t('profile.usage_loading')}</p>

  const locale = i18n.language
  const resets = new Date(status.resets_at).toLocaleString(locale, {
    hour: '2-digit', minute: '2-digit', day: 'numeric', month: 'short',
  })
  const pct = Math.round(usageFraction(status) * 100)

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium text-foreground">
          {t('profile.usage_plan', { plan: status.plan })}
        </span>
        <span className="text-sm text-muted-foreground">
          {status.limit === null
            ? t('profile.usage_unlimited')
            : t('profile.usage_of', { used: formatTokens(status.used, locale), limit: formatTokens(status.limit, locale) })}
        </span>
      </div>
      {status.limit !== null && (
        <div
          className="h-2 rounded-full bg-secondary/60 overflow-hidden"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
        >
          <div
            className={`h-full rounded-full ${pct >= 100 ? 'bg-destructive' : 'bg-primary'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
      <p className="text-xs text-muted-foreground">{t('profile.usage_resets', { time: resets })}</p>
    </div>
  )
}
