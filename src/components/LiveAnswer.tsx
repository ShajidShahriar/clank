import { memo } from 'react'
import { Loader2 } from 'lucide-react'
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
    <div className="min-w-0">
      {live.text !== '' && (
        <div className="selectable min-w-0 text-sm leading-[1.6] text-label">
          <Markdown text={live.text} />
        </div>
      )}
      <div role="status" aria-live="off" className={`flex items-center gap-1.5 text-xs text-label-2 ${live.text !== '' ? 'mt-3' : ''}`}>
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />
        <span className="tabular-nums">{stageLabel(live, now)}</span>
      </div>
      <SourcesList sources={sources} onOpen={onOpenSource} />
    </div>
  )
}

export default memo(LiveAnswer)
