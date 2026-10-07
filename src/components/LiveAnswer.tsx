import { memo } from 'react'
import { Bot, Loader2 } from 'lucide-react'
import type { Source } from '../api/types'
import { stageLabel } from '../lib/liveAnswer'
import type { Entry } from '../types'
import { useNow } from '../hooks/useNow'
import Markdown from './Markdown'
import SourcesList from './SourcesList'

/** An answer while it is being written: where it is (with a running timer), the sources as soon as the search is done, and the text so far. */
function LiveAnswer({ entry, onOpenSource }: { entry: Entry, onOpenSource: (source: Source) => void }) {
  const now = useNow(true)
  const live = entry.live
  if (!live) return null
  const sources = live.start?.sources ?? []
  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800">
        <Bot className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
      </div>
      <div className="min-w-0 max-w-[85%] rounded-lg border border-gray-200 bg-white px-3.5 py-2.5 text-sm leading-relaxed text-gray-900 dark:border-white/10 dark:bg-[#0a0a0a] dark:text-gray-100">
        {live.text !== '' && <Markdown text={live.text} />}
        <div role="status" aria-live="off" className={`flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 ${live.text !== '' ? 'mt-2' : ''}`}>
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
          <span className="tabular-nums">{stageLabel(live, now)}</span>
        </div>
        <SourcesList sources={sources} onOpen={onOpenSource} />
      </div>
    </div>
  )
}

export default memo(LiveAnswer)
