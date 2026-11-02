import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Switch } from '@/components/ui/switch'
import { setPluginEnabled, type PluginInfo } from '@/lib/api'
import { statusKey } from '@/lib/plugins'

interface PluginsContentProps {
  plugins: PluginInfo[]
  /** The server's answer for the plugin that was just switched. */
  onChange: (updated: PluginInfo) => void
}

/**
 * One switch per plugin, with the plugin's status as text. Shown in the
 * settings modal like the Language and Memory views: the modal header carries
 * the title and the back button, so this view adds no header of its own.
 */
export default function PluginsContent({ plugins, onChange }: PluginsContentProps) {
  const { t } = useTranslation()
  const [pending, setPending] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

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

  return (
    <div className="flex-1 overflow-y-auto p-6">
      {plugins.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('plugins.empty')}</p>
      ) : (
        <ul className="space-y-4">
          {plugins.map((plugin) => (
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
          ))}
        </ul>
      )}
    </div>
  )
}
