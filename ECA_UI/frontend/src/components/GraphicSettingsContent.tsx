import { MonitorCog } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Switch } from './ui/switch'
import { useGraphics, type GraphicsSettings } from '../hooks/useGraphics'

interface Section {
  title: string
  items: {
    key: keyof GraphicsSettings
    label: string
    description: string
  }[]
}

export default function GraphicSettingsContent() {
  const { t } = useTranslation()
  const { settings, setSetting } = useGraphics()

  const SECTIONS: Section[] = [
    {
      title: t('graphics.performance'),
      items: [
        { key: 'ssao', label: t('graphics.ssao'), description: t('graphics.ssao_desc') },
        { key: 'particles', label: t('graphics.particles'), description: t('graphics.particles_desc') },
      ],
    },
    {
      title: t('graphics.visual'),
      items: [
        { key: 'vignette', label: t('graphics.vignette'), description: t('graphics.vignette_desc') },
        { key: 'mtoon', label: t('graphics.mtoon'), description: t('graphics.mtoon_desc') },
      ],
    },
    {
      title: t('graphics.debug'),
      items: [
        { key: 'showGrid', label: t('graphics.show_grid'), description: t('graphics.show_grid_desc') },
        { key: 'showAxes', label: t('graphics.show_axes'), description: t('graphics.show_axes_desc') },
      ],
    },
  ]

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center gap-2 mb-6">
        <MonitorCog className="w-5 h-5 text-muted-foreground" />
        <h2 className="text-lg font-semibold text-foreground">{t('settings.graphics')}</h2>
      </div>

      <div className="space-y-6">
        {SECTIONS.map((section) => (
          <div key={section.title}>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
              {section.title}
            </h3>
            <div className="space-y-1">
              {section.items.map((item) => (
                <div
                  key={item.key}
                  className="flex justify-between items-center px-4 py-3 rounded-xl hover:bg-secondary/40 transition-colors"
                >
                  <div>
                    <span className="text-sm font-medium text-foreground">{item.label}</span>
                    <p className="text-xs text-muted-foreground mt-0.5">{item.description}</p>
                  </div>
                  <Switch
                    checked={settings[item.key]}
                    onCheckedChange={(v) => setSetting(item.key, v)}
                  />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

