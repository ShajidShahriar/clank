import { ChevronRight, FileCode } from 'lucide-react'
import type { Source } from '../api/types'
import { sourceLabel } from '../lib/present'

/** The code the answer was based on: file and lines, the symbol, how close it matched, and whether the file changed since it was indexed. */
function SourcesList({ sources, onOpen }: { sources: Source[], onOpen: (source: Source) => void }) {
  if (sources.length === 0) return null
  return (
    <details className="group mt-3 text-xs text-label-2">
      <summary className="press flex w-fit cursor-pointer select-none list-none items-center gap-1 rounded-full bg-fill px-2.5 py-1 font-medium text-label-2 hover:bg-fill-strong hover:text-label [&::-webkit-details-marker]:hidden">
        <ChevronRight className="h-3 w-3 transition-transform duration-200 group-open:rotate-90" strokeWidth={2} />
        Sources ({sources.length})
      </summary>
      <ul className="mt-2 flex flex-col overflow-hidden rounded-card bg-card">
        {sources.map((source, i) => (
          <li key={`${source.path}-${source.start_line}-${i}`} className="border-line [&:not(:first-child)]:border-t">
            <button onClick={() => onOpen(source)} title={`Show ${sourceLabel(source)}`} className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-fill">
              <FileCode className="h-3.5 w-3.5 shrink-0 text-label-3" strokeWidth={1.5} />
              <span className="min-w-0 truncate font-mono text-[11px] text-accent">{sourceLabel(source)}</span>
              {source.symbol && <span className="truncate text-label-3">{source.parent ? `${source.parent}.${source.symbol}` : source.symbol}</span>}
              <span className="ml-auto shrink-0 tabular-nums text-label-3">{source.score.toFixed(2)}</span>
              {source.stale && <span className="shrink-0 rounded-full bg-warn-soft px-1.5 text-[10px] font-semibold text-warn">STALE</span>}
            </button>
          </li>
        ))}
      </ul>
    </details>
  )
}

export default SourcesList
