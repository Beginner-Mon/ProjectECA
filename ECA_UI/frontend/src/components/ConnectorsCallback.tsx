import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { completeConnector } from '@/lib/api'
import { parseCallbackQuery } from '@/lib/connectors'

/**
 * The OAuth callback page at `/connectors/callback`.
 *
 * Reads `code`, `state` and `iss` from the query string, posts them to
 * `POST /connectors/callback`, then returns to the chat. The code and state
 * are never logged or displayed, and are stripped from the URL
 * (`history.replaceState`) immediately after being read.
 */
export default function ConnectorsCallback() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<'connecting' | 'connected' | 'error'>('connecting')
  const [message, setMessage] = useState(t('connectors.callback_connecting'))

  useEffect(() => {
    let cancelled = false

    const search = window.location.search
    // Strip the sensitive params from the URL right after reading them, before
    // anything else can record them (referrer, history, logs).
    window.history.replaceState(null, '', window.location.pathname)

    let parsed: ReturnType<typeof parseCallbackQuery>
    try {
      parsed = parseCallbackQuery(search)
    } catch {
      parsed = { error: 'missing_parameter' }
    }

    if ('error' in parsed) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStatus('error')
      setMessage(t('connectors.callback_error'))
      return
    }

    const { code, state, iss } = parsed
    setStatus('connecting')

    completeConnector({ code, state, iss })
      .then(() => {
        if (cancelled) return
        setStatus('connected')
        setMessage(t('connectors.callback_connected'))
        // Return to the chat once the connection is sealed.
        setTimeout(() => { window.location.assign('/') }, 1200)
      })
      .catch(() => {
        if (cancelled) return
        setStatus('error')
        setMessage(t('connectors.callback_error'))
      })

    return () => { cancelled = true }
  }, [t])

  return (
    <div className="flex flex-col items-center justify-center min-h-screen text-muted-foreground">
      <p className="text-sm">{status === 'connected'
        ? t('connectors.callback_connected')
        : status === 'error'
          ? t('connectors.callback_error')
          : t('connectors.callback_connecting')}</p>
      <p className="text-xs mt-2">{message}</p>
    </div>
  )
}
