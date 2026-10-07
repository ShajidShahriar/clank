import { AlertTriangle, Download, Loader2, RefreshCw } from 'lucide-react'
import type { SetupView } from '../lib/setup'
import { BUTTON_PRIMARY } from './ui'

const BUTTON = BUTTON_PRIMARY

interface SetupCardProps {
  view: SetupView
  busy: boolean
  actionError: string | null
  onPull: () => void
  onCheck: () => void
}


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
    <div role="status" className="animate-rise mx-5 mb-1 shrink-0 rounded-card bg-accent-soft px-4 py-3 text-xs text-label">
      <div className="flex items-start gap-3">
        {view.kind === 'pulling' ? <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-accent" /> : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-accent" strokeWidth={1.75} />}
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold">{title}</p>
          <p className="selectable mt-0.5 leading-relaxed text-label-2">{body}</p>
          {view.kind === 'pulling' && (
            <div className="mt-2">
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-fill-strong" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={view.percent ?? undefined}>
                <div className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out" style={{ width: `${view.percent ?? 0}%` }} />
              </div>
              <p className="mt-1 tabular-nums opacity-80">{[view.percent === null ? 'Starting…' : `${view.percent}%`, view.summary].filter(Boolean).join(' · ')}</p>
            </div>
          )}
          {actionError && <p className="selectable mt-1.5 text-danger">{actionError}</p>}
        </div>
        {action}
      </div>
    </div>
  )
}

export default SetupCard
