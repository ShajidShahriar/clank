import { AlertTriangle, Loader2 } from 'lucide-react'
import type { BackendView } from '../types'

interface BackendBannerProps {
  backend: BackendView
  onRestart: () => void
}

/** Shown only while the backend is not ready: starting, crashed, failed or stopped (or the page is open in a plain browser). */
function BackendBanner({ backend, onRestart }: BackendBannerProps) {
  if (backend.state === 'ready') return null
  const starting = backend.state === 'starting'
  return (
    <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-900 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-200">
      <div className="flex items-center gap-2">
        {starting ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
        <span className="flex-1">{backend.message}</span>
        {backend.state !== 'no_bridge' && !starting && (
          <button onClick={onRestart} className="shrink-0 rounded-md border border-amber-300 px-2 py-0.5 font-medium hover:bg-amber-100 dark:border-amber-500/30 dark:hover:bg-amber-500/10">
            Restart backend
          </button>
        )}
      </div>
      {backend.detail.length > 0 && (
        <pre className="mt-1.5 max-h-24 overflow-auto whitespace-pre-wrap font-mono text-[11px] opacity-80">{backend.detail.join('\n')}</pre>
      )}
    </div>
  )
}

export default BackendBanner
