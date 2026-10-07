import { memo } from 'react'
import { AlertTriangle } from 'lucide-react'
import type { Entry } from '../types'
import { describeError, type ErrorAction } from '../lib/present'
import { useCountdown } from '../hooks/useCountdown'

interface ErrorCardProps {
  entry: Entry
  onAction: (action: ErrorAction, entry: Entry) => void
}

const LABELS: Partial<Record<ErrorAction, string>> = {
  allow_remote: 'Allow and ask again',
  index: 'Index now',
  restart_backend: 'Restart backend',
  retry: 'Try again',
  wait: 'Try again',
  refresh: 'Refresh projects',
  settings: 'Open settings',
}

/** What went wrong, in the backend's own words, and the one button that makes sense next. */
function ErrorCard({ entry, onAction }: ErrorCardProps) {
  const described = entry.error ? describeError(entry.error) : { message: 'Something went wrong.', action: 'retry' as ErrorAction, retryAfterSeconds: null }
  const left = useCountdown(described.retryAfterSeconds)
  const label = LABELS[described.action]
  const waiting = described.action === 'wait' && left > 0

  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-red-50 dark:bg-red-500/10">
        <AlertTriangle className="h-3.5 w-3.5 text-red-600 dark:text-red-400" />
      </div>
      <div className="max-w-[85%] rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-900 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-200">
        <p className="whitespace-pre-wrap break-words">{described.message}</p>
        {label && (
          <button
            onClick={() => onAction(described.action, entry)}
            disabled={waiting}
            className="mt-2 rounded-md border border-red-300 px-2.5 py-1 text-xs font-medium hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-red-500/30 dark:hover:bg-red-500/10"
          >
            {waiting ? `${label} in ${left}s` : label}
          </button>
        )}
      </div>
    </div>
  )
}

export default memo(ErrorCard)
