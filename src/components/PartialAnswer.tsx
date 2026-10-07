import { memo } from 'react'
import { RotateCcw } from 'lucide-react'
import type { ErrorAction } from '../lib/present'
import type { Entry } from '../types'
import { BUTTON_SMALL } from './ui'
import Markdown from './Markdown'

/** What was written of an answer that was stopped, or that failed in the middle. It was not saved: the person can ask again. */
function PartialAnswer({ entry, onAction }: { entry: Entry, onAction: (action: ErrorAction, entry: Entry) => void }) {
  const stopped = entry.state === 'stopped'
  return (
    <div className="min-w-0">
      {entry.partial && (
        <div className="selectable min-w-0 text-sm leading-[1.6] text-label-2">
          <Markdown text={entry.partial} />
        </div>
      )}
      <div className={`flex items-center gap-2 text-xs text-label-3 ${entry.partial ? 'mt-3' : ''}`}>
        <span>{stopped ? 'Stopped. This answer was not saved.' : 'The answer stopped early and was not saved.'}</span>
        {stopped && (
          <button onClick={() => onAction('retry', entry)} className={BUTTON_SMALL}>
            <RotateCcw className="h-3 w-3" strokeWidth={1.75} />
            Ask again
          </button>
        )}
      </div>
    </div>
  )
}

export default memo(PartialAnswer)
