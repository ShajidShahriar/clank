import { memo } from 'react'
import { Bot, RotateCcw } from 'lucide-react'
import type { ErrorAction } from '../lib/present'
import type { Entry } from '../types'
import Markdown from './Markdown'

/** What was written of an answer that was stopped, or that failed in the middle. It was not saved: the person can ask again. */
function PartialAnswer({ entry, onAction }: { entry: Entry, onAction: (action: ErrorAction, entry: Entry) => void }) {
  const stopped = entry.state === 'stopped'
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800">
        <Bot className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
      </div>
      <div className="min-w-0 max-w-[85%] rounded-lg border border-dashed border-gray-300 bg-white px-3.5 py-2.5 text-sm leading-relaxed text-gray-700 dark:border-white/20 dark:bg-[#0a0a0a] dark:text-gray-300">
        {entry.partial && <Markdown text={entry.partial} />}
        <div className={`flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 ${entry.partial ? 'mt-2' : ''}`}>
          <span>{stopped ? 'Stopped. This answer was not saved.' : 'The answer stopped early and was not saved.'}</span>
          {stopped && (
            <button onClick={() => onAction('retry', entry)} className="flex items-center gap-1 rounded-md border border-gray-300 px-2 py-0.5 font-medium text-gray-700 hover:bg-gray-100 dark:border-white/20 dark:text-gray-300 dark:hover:bg-gray-800">
              <RotateCcw className="h-3 w-3" />
              Ask again
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(PartialAnswer)
