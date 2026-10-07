import { AlertTriangle, Loader2 } from 'lucide-react'
import type { BackendView } from '../types'
import { BUTTON_SMALL } from './ui'

interface BackendBannerProps {
  backend: BackendView
  onRestart: () => void
}

/** Shown only while the backend is not ready: starting, crashed, failed or stopped (or the page is open in a plain browser). */
function BackendBanner({ backend, onRestart }: BackendBannerProps) {
  if (backend.state === 'ready') return null
  const starting = backend.state === 'starting'
  return (
    <div role="status" className="shrink-0 bg-warn-soft px-5 py-2 text-xs text-warn">
      <div className="flex items-center gap-2">
        {starting ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
        <span className="flex-1">{backend.message}</span>
        {backend.state !== 'no_bridge' && !starting && (
          <button onClick={onRestart} className={BUTTON_SMALL}>
            Restart backend
          </button>
        )}
      </div>
      {backend.detail.length > 0 && (
        <pre className="selectable mt-1.5 max-h-24 overflow-auto whitespace-pre-wrap font-mono text-[11px] opacity-80">{backend.detail.join('\n')}</pre>
      )}
    </div>
  )
}

export default BackendBanner
