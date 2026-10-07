import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'
import type { createApi } from '../api/client'
import type { ApiError, Source, SourceView } from '../api/types'
import { describeError, sourceLabel } from '../lib/present'
import { describeShown, mergeViews, moreRange, referenceRows, requestRange, rowsFor, viewNotice, type Row } from '../lib/sourceView'

type Api = ReturnType<typeof createApi>

interface SourceViewerProps {
  api: Api
  projectId: number
  source: Source
  onClose: () => void
}

/** The numbered lines; the marked ones (the source's own) have a tint. */
export function SourceLines({ rows, markedRef, firstMarkedLine }: { rows: Row[], markedRef?: React.Ref<HTMLTableRowElement>, firstMarkedLine?: number }) {
  return (
    <table className="w-full border-collapse font-mono text-[12px] leading-5">
      <tbody>
        {rows.map((row) => (
          <tr key={row.n} ref={row.n === firstMarkedLine ? markedRef : undefined} className={row.marked ? 'bg-blue-50 dark:bg-blue-500/10' : ''} data-marked={row.marked || undefined}>
            <td className="select-none whitespace-nowrap px-3 text-right align-top tabular-nums text-gray-400 dark:text-gray-600">{row.n}</td>
            <td className="whitespace-pre pr-4 text-gray-900 dark:text-gray-100">{row.text === '' ? ' ' : row.text}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function ViewerToolbar({ summary, browsing, onToggle }: { summary: string, browsing: boolean, onToggle: () => void }) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-4 py-1.5 text-xs text-gray-500 dark:border-white/10 dark:text-gray-400">
      <span className="flex-1 tabular-nums">{summary}</span>
      <button onClick={onToggle} className="shrink-0 rounded-md border border-gray-200 px-2 py-0.5 font-medium text-gray-700 hover:bg-gray-100 dark:border-white/10 dark:text-gray-300 dark:hover:bg-gray-800">
        {browsing ? 'Show only these lines' : 'Browse the file'}
      </button>
    </div>
  )
}

export function MoreButton({ label, busy, disabled, onClick }: { label: string, busy: boolean, disabled: boolean, onClick: () => void }) {
  return (
    <button onClick={onClick} disabled={disabled} className="flex w-full items-center justify-center gap-1.5 border-y border-gray-100 py-1.5 text-xs text-gray-500 hover:bg-gray-50 hover:text-gray-900 disabled:cursor-wait disabled:opacity-60 dark:border-white/5 dark:hover:bg-gray-900 dark:hover:text-white">
      {busy && <Loader2 className="h-3 w-3 animate-spin" />}
      {label}
    </button>
  )
}

/** A read-only look at the lines a source points to, with a little code around them. The lines come from the project's own file, through the backend's strict viewer. */
function SourceViewer({ api, projectId, source, onClose }: SourceViewerProps) {
  const [view, setView] = useState<SourceView | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [browsing, setBrowsing] = useState(false)                                                   // false: only the lines the answer points to; true: the file, with paging
  const [loadingMore, setLoadingMore] = useState<'before' | 'after' | null>(null)
  const [moreError, setMoreError] = useState<ApiError | null>(null)
  const firstMarked = useRef<HTMLTableRowElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const anchor = useRef<{ height: number, top: number } | null>(null)

  useEffect(() => {
    let alive = true
    const range = requestRange(source.start_line, source.end_line)
    api.getSource(projectId, source.path, range.start, range.end).then((result) => {
      if (!alive) return
      if (result.ok) setView(result.data)
      else setError(result.error)
    })
    return () => { alive = false }
  }, [api, projectId, source])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useLayoutEffect(() => {                                                                          // lines added ABOVE push the text down: keep the same text under the eye
    const el = scroller.current
    if (anchor.current && el) el.scrollTop = anchor.current.top + (el.scrollHeight - anchor.current.height)
    anchor.current = null
  }, [view])

  const opened = view !== null
  useEffect(() => {                                                                                // when the panel fills or the mode changes (more lines must not move the page)
    if (browsing) firstMarked.current?.scrollIntoView({ block: 'center' })
    else if (scroller.current) scroller.current.scrollTop = 0
  }, [opened, browsing])

  const showMore = async (direction: 'before' | 'after') => {
    const range = view ? moreRange(view, direction) : null
    if (!view || !range || loadingMore) return
    setLoadingMore(direction)
    setMoreError(null)
    const el = scroller.current
    if (direction === 'before' && el) anchor.current = { height: el.scrollHeight, top: el.scrollTop }
    const result = await api.getSource(projectId, source.path, range.start, range.end)
    setLoadingMore(null)
    if (!result.ok) anchor.current = null
    if (result.ok) setView((current) => (current ? mergeViews(current, result.data) : current))
    else setMoreError(result.error)
  }

  const allRows = useMemo(() => (view ? rowsFor(view, source) : []), [view, source])
  const rows = browsing ? allRows : referenceRows(allRows)
  const notice = view ? viewNotice(view) : null
  const firstMarkedLine = rows.find((row) => row.marked)?.n
  const canShowMore = browsing && view !== null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }} role="dialog" aria-modal="true" aria-label="Source code">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl dark:border-white/10 dark:bg-[#0a0a0a]">
        <div className="flex shrink-0 items-center gap-2 border-b border-gray-200 px-4 py-2.5 dark:border-white/10">
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-900 dark:text-gray-100" title={sourceLabel(source)}>{sourceLabel(source)}</span>
          <button onClick={onClose} aria-label="Close" className="shrink-0 rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-800 dark:hover:text-white">
            <X className="h-4 w-4" />
          </button>
        </div>
        {view && <ViewerToolbar summary={describeShown(rows, view.total_lines)} browsing={browsing} onToggle={() => setBrowsing((value) => !value)} />}
        {notice && <p className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-xs text-amber-800 dark:border-amber-500/20 dark:bg-amber-500/10 dark:text-amber-300">{notice}</p>}
        <div ref={scroller} className="min-h-0 flex-1 overflow-auto">
          {!view && !error && (
            <p className="flex items-center gap-2 p-4 text-xs text-gray-500 dark:text-gray-400"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading the lines…</p>
          )}
          {error && <p className="p-4 text-xs text-red-700 dark:text-red-300">{describeError(error).message}</p>}
          {view && (
            <>
              {canShowMore && moreRange(view, 'before') && <MoreButton label="Show earlier lines" busy={loadingMore === 'before'} disabled={loadingMore !== null} onClick={() => void showMore('before')} />}
              <SourceLines rows={rows} markedRef={firstMarked} firstMarkedLine={firstMarkedLine} />
              {canShowMore && moreRange(view, 'after') && <MoreButton label="Show more lines" busy={loadingMore === 'after'} disabled={loadingMore !== null} onClick={() => void showMore('after')} />}
              {moreError && <p className="px-4 py-2 text-xs text-red-700 dark:text-red-300">{describeError(moreError).message}</p>}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default SourceViewer
