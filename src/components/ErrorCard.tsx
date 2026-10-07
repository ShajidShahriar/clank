import { memo } from 'react'
import { AlertTriangle } from 'lucide-react'
import type { Entry } from '../types'
import { describeError, type ErrorAction } from '../lib/present'
import { useCountdown } from '../hooks/useCountdown'
import { BUTTON_SMALL } from './ui'

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
    <div role="alert" className="animate-rise flex max-w-[560px] items-start gap-2.5 rounded-card bg-danger-soft px-3.5 py-3">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" strokeWidth={1.75} />
      <div className="min-w-0 text-[13px] text-label">
        <p className="selectable whitespace-pre-wrap break-words">{described.message}</p>
        {label && (
          <button onClick={() => onAction(described.action, entry)} disabled={waiting} className={`${BUTTON_SMALL} mt-2.5`}>
            {waiting ? `${label} in ${left}s` : label}
          </button>
        )}
      </div>
    </div>
  )
}

export default memo(ErrorCard)
