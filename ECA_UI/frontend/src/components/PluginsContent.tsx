import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import {
  deleteConnector,
  listConnectors,
  setPluginEnabled,
  startConnector,
  type ConnectorInfo,
  type PluginInfo,
} from '@/lib/api'
import { statusKey } from '@/lib/plugins'
import { controlFor, type ConnectorControl } from '@/lib/connectors'

interface PluginsContentProps {
  plugins: PluginInfo[]
  /** The server's answer for the plugin that was just switched. */
  onChange: (updated: PluginInfo) => void
}

/**
 * One row per plugin. OAuth connectors (manifest auth.type == "mcp_oauth")
 * show their connection status and one control — Connect, Disconnect when
 * connected, or Reconnect when auth_expired — instead of the on/off switch.
 * The panel reads `GET /me/connectors` for each plugin's connection status.
 * Shown in the settings modal like the Language and Memory views: the modal
 * header carries the title and the back button, so this view adds no header
 * of its own.
 */
export default function PluginsContent({ plugins, onChange }: PluginsContentProps) {
  const { t } = useTranslation()
  const [connections, setConnections] = useState<ConnectorInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    listConnectors()
      .then((answer) => { if (alive) { setConnections(answer.connections); setLoading(false) } })
      .catch(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  const connectionOf = (pluginId: string): ConnectorInfo | null =>
    connections.find((c) => c.plugin_id === pluginId) ?? null

  const toggle = async (plugin: PluginInfo, enabled: boolean) => {
    if (pending) return
    setPending(plugin.id)
    setFailed(null)
    try {
      onChange(await setPluginEnabled(plugin.id, enabled))
    } catch {
      setFailed(plugin.id)
    } finally {
      setPending(null)
    }
  }

  const connect = async (plugin: PluginInfo) => {
    if (pending) return
    setPending(plugin.id)
    setFailed(null)
    try {
      const { authorize_url } = await startConnector(plugin.id)
      window.location.assign(authorize_url)
    } catch {
      setFailed(plugin.id)
    } finally {
      setPending(null)
    }
  }

  const disconnect = async (plugin: PluginInfo) => {
    if (pending) return
    setPending(plugin.id)
    setFailed(null)
    try {
      await deleteConnector(plugin.id)
      onChange(plugin)
    } catch {
      setFailed(plugin.id)
    } finally {
      setPending(null)
    }
  }

  const oauthRow = (plugin: PluginInfo) => {
    const connection = connectionOf(plugin.id)
    const control: ConnectorControl = controlFor(connection)
    const label = control === 'connect'
      ? t('connectors.connect')
      : control === 'disconnect'
        ? t('connectors.disconnect')
        : t('connectors.reconnect')
    const statusText = connection
      ? t(`connectors.status_${connection.status === 'auth_expired' ? 'expired' : 'connected'}`)
      : t('connectors.not_connected')
    const isPending = pending === plugin.id
    return (
      <li key={plugin.id} className="flex items-start justify-between gap-4 rounded-xl border border-border/40 bg-card px-6 py-5">
        <div className="min-w-0">
          <p className="text-base font-semibold text-foreground">{plugin.name}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{t('plugins.by', { publisher: plugin.publisher })}</p>
          <p className="text-sm text-muted-foreground mt-2">{plugin.description}</p>
          <p className={`text-xs font-medium mt-2 ${connection && connection.status === 'active'
            ? 'text-foreground' : 'text-muted-foreground'}`}>{statusText}</p>
          {failed === plugin.id && (
            <p role="alert" className="text-sm text-destructive mt-2">{t('connectors.error')}</p>
          )}
        </div>
        <Button
          onClick={() => (control === 'disconnect' ? disconnect(plugin) : connect(plugin))}
          disabled={isPending || loading}
          className="mt-1"
        >
          {isPending ? t('connectors.connecting') : label}
        </Button>
      </li>
    )
  }

  const switchRow = (plugin: PluginInfo) => (
    <li key={plugin.id} className="flex items-start justify-between gap-4 rounded-xl border border-border/40 bg-card px-6 py-5">
      <div className="min-w-0">
        <p className="text-base font-semibold text-foreground">{plugin.name}</p>
        <p className="text-xs text-muted-foreground mt-0.5">{t('plugins.by', { publisher: plugin.publisher })}</p>
        <p className="text-sm text-muted-foreground mt-2">{plugin.description}</p>
        <p className="text-xs font-medium text-foreground mt-2">{t(`plugins.status_${statusKey(plugin.status)}`)}</p>
        {failed === plugin.id && (
          <p role="alert" className="text-sm text-destructive mt-2">{t('plugins.save_failed')}</p>
        )}
      </div>
      <Switch
        checked={plugin.enabled}
        disabled={pending !== null}
        onCheckedChange={(checked) => void toggle(plugin, checked)}
        aria-label={plugin.name}
        className="mt-1"
      />
    </li>
  )

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {loading ? (
        <p className="text-sm text-muted-foreground">{t('common.loading')}</p>
      ) : plugins.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('plugins.empty')}</p>
      ) : (
        <ul className="space-y-4">
          {plugins.map((plugin) =>
            plugin.authType === 'mcp_oauth' ? oauthRow(plugin) : switchRow(plugin),
          )}
        </ul>
      )}
    </div>
  )
}
