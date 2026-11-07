import { useId } from 'react'
import { ECA_LOGO_PATH, ECA_LOGO_TRANSFORM, ECA_LOGO_VIEWBOX } from '../lib/ecaLogo'

export default function EcaLogo({
  className,
  gradient = false,
}: {
  className?: string
  gradient?: boolean
}) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, '')
  return (
    <svg
      className={className}
      viewBox={ECA_LOGO_VIEWBOX}
      fill={gradient ? `url(#eca-gradient-${id})` : 'currentColor'}
      stroke="none"
    >
      <defs>
        <linearGradient id={`eca-gradient-${id}`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#60a5fa" />
          <stop offset="50%" stopColor="#a855f7" />
          <stop offset="100%" stopColor="#f472b6" />
        </linearGradient>
      </defs>
      <g transform={ECA_LOGO_TRANSFORM}>
      <path d={ECA_LOGO_PATH} />
      </g>
    </svg>
  )
}
