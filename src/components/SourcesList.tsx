import { FileCode } from 'lucide-react'
import type { Source } from '../api/types'
import { sourceLabel } from '../lib/present'

/** The code the answer was based on: file and lines, the symbol, how close it matched, and whether the file changed since it was indexed. */
function SourcesList({ sources, onOpen }: { sources: Source[], onOpen: (source: Source) => void }) {
  if (sources.length === 0) return null
  return (
    <details className="mt-2 text-xs text-gray-600 dark:text-gray-400">
      <summary className="cursor-pointer select-none font-medium text-gray-700 hover:text-gray-900 dark:text-gray-300 dark:hover:text-white">
        Sources ({sources.length})
      </summary>
      <ul className="mt-1.5 flex flex-col gap-1">
        {sources.map((source, i) => (
          <li key={`${source.path}-${source.start_line}-${i}`} className="flex items-center gap-1.5">
            <FileCode className="h-3.5 w-3.5 shrink-0 opacity-60" />
            <button onClick={() => onOpen(source)} title={`Show ${sourceLabel(source)}`} className="min-w-0 truncate text-left font-mono text-[11px] text-gray-800 underline-offset-2 hover:text-blue-700 hover:underline dark:text-gray-200 dark:hover:text-blue-300">{sourceLabel(source)}</button>
            {source.symbol && <span className="truncate opacity-70">{source.parent ? `${source.parent}.${source.symbol}` : source.symbol}</span>}
            <span className="ml-auto shrink-0 tabular-nums opacity-60">{source.score.toFixed(2)}</span>
            {source.stale && <span className="shrink-0 rounded border border-amber-300 px-1 text-[10px] font-medium text-amber-700 dark:border-amber-500/40 dark:text-amber-300">STALE</span>}
          </li>
        ))}
      </ul>
    </details>
  )
}

export default SourcesList
