import { AlertTriangle, Download, Loader2, RefreshCw } from 'lucide-react'
import type { SetupView } from '../lib/setup'

interface SetupCardProps {
  view: SetupView
  busy: boolean
  actionError: string | null
  onPull: () => void
  onCheck: () => void
}

const BUTTON = 'flex shrink-0 items-center gap-1.5 rounded-md border border-gray-300 px-2.5 py-1 text-xs font-medium text-gray-900 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/20 dark:text-white dark:hover:bg-gray-800'

/** The first-run card: what Clank is missing to read code (Ollama, or its search model), and the one thing to do about it. Nothing when all is well. */
function SetupCard({ view, busy, actionError, onPull, onCheck }: SetupCardProps) {
  if (view.kind === 'none') return null

  let title = ''
  let body = ''
  let action: React.ReactNode = null
  if (view.kind === 'ollama_down') {
    title = 'Ollama is not running'
    body = 'Clank uses Ollama to read your code on this computer. Open the Ollama app. If you have not installed it yet, get it from ollama.com first.'
    action = <button onClick={onCheck} className={BUTTON}><RefreshCw className="h-3.5 w-3.5" />Check again</button>
  } else if (view.kind === 'model_missing') {
    title = 'One more download'
    body = 'Clank needs a small search model (about 640 MB) to understand code. It runs on this computer, and your code is not sent anywhere to use it.'
    action = <button onClick={onPull} disabled={busy} className={BUTTON}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}Download the model</button>
  } else if (view.kind === 'pulling') {
    title = 'Downloading the search model'
    body = 'This takes a few minutes. Keep Clank open.'
  } else if (view.kind === 'pull_failed') {
    title = 'The download did not finish'
    body = view.error
    action = <button onClick={onPull} disabled={busy} className={BUTTON}>{busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}Try again</button>
  } else {
    title = 'The search model is not ready'
    body = view.detail
    action = <button onClick={onCheck} className={BUTTON}><RefreshCw className="h-3.5 w-3.5" />Check again</button>
  }

  return (
    <div role="status" className="shrink-0 border-b border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-950 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-100">
      <div className="flex items-start gap-3">
        {view.kind === 'pulling' ? <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
        <div className="min-w-0 flex-1">
          <p className="font-medium">{title}</p>
          <p className="mt-0.5 leading-relaxed opacity-90">{body}</p>
          {view.kind === 'pulling' && (
            <div className="mt-2">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-blue-200 dark:bg-blue-500/20" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={view.percent ?? undefined}>
                <div className="h-full rounded-full bg-blue-600 transition-all dark:bg-blue-400" style={{ width: `${view.percent ?? 0}%` }} />
              </div>
              <p className="mt-1 tabular-nums opacity-80">{[view.percent === null ? 'Starting…' : `${view.percent}%`, view.summary].filter(Boolean).join(' · ')}</p>
            </div>
          )}
          {actionError && <p className="mt-1.5 text-red-700 dark:text-red-300">{actionError}</p>}
        </div>
        {action}
      </div>
    </div>
  )
}

export default SetupCard
