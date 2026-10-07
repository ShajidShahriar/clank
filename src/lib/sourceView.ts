// How the source viewer decides which lines to ask for and which to mark. Pure (no React, no browser), tested in sourceView.test.ts. Erasable TypeScript only.
import type { SourceView } from '../api/types.ts'

export const CONTEXT_LINES = 12            // lines shown before and after the source's own lines
export const MAX_VIEW_LINES = 500          // what the backend allows in one request (backend/source_view.py MAX_VIEW_LINES)

/** The lines to ask for: the source's own lines plus context. The context shrinks first when the limit is near; a source longer than the limit is cut to its first lines. */
export function requestRange(startLine: number, endLine: number): { start: number, end: number } {
  const own = Math.min(endLine - startLine + 1, MAX_VIEW_LINES)
  const spare = MAX_VIEW_LINES - own
  const before = Math.min(CONTEXT_LINES, startLine - 1, spare)
  const after = Math.min(CONTEXT_LINES, spare - before)
  return { start: startLine - before, end: startLine + own - 1 + after }
}

export type Row = { n: number, text: string, marked: boolean }

/** One row per line the backend sent, with its real line number, marked when it is one of the source's lines. */
export function rowsFor(view: SourceView, source: { start_line: number, end_line: number }): Row[] {
  return view.lines.map((text, i) => {
    const n = view.start_line + i
    return { n, text, marked: n >= source.start_line && n <= source.end_line }
  })
}

export function viewNotice(view: SourceView): string | null {
  return view.stale ? 'This file changed after it was indexed. The lines shown are the current ones and may not match the answer.' : null
}

export const PAGE_LINES = 100              // how many more lines one press of "show more" asks for

/** The next lines to ask for above or below what is shown, or null when the start or the end of the file is already on screen. */
export function moreRange(view: SourceView, direction: 'before' | 'after'): { start: number, end: number } | null {
  if (direction === 'before') {
    return view.start_line <= 1 ? null : { start: Math.max(1, view.start_line - PAGE_LINES), end: view.start_line - 1 }
  }
  return view.end_line >= view.total_lines ? null : { start: view.end_line + 1, end: Math.min(view.total_lines, view.end_line + PAGE_LINES) }
}

/** Join more lines to what is shown. Only lines that touch it directly (the very next line above or below) are joined; anything else is ignored, so the
 *  numbering can never get a gap or a repeat. The line count is the newest one; the file counts as changed if either part says so. */
export function mergeViews(current: SourceView, more: SourceView): SourceView {
  const before = more.end_line + 1 === current.start_line
  const after = more.start_line === current.end_line + 1
  if (!before && !after) return current
  return {
    ...current,
    start_line: before ? more.start_line : current.start_line,
    end_line: after ? more.end_line : current.end_line,
    total_lines: more.total_lines,
    stale: current.stale || more.stale,
    lines: before ? [...more.lines, ...current.lines] : [...current.lines, ...more.lines],
  }
}

/** What the panel shows first: only the lines the answer points to (the rows the backend sent as context are kept for "browse the file"). */
export function referenceRows(rows: Row[]): Row[] {
  return rows.filter((row) => row.marked)
}

/** "Lines 10-12 of 342": which lines are on screen and how big the file is. */
export function describeShown(rows: Row[], totalLines: number): string {
  if (rows.length === 0) return 'No lines to show'
  const first = rows[0].n
  const last = rows[rows.length - 1].n
  const total = totalLines.toLocaleString('en-US')
  return first === last ? `Line ${first} of ${total}` : `Lines ${first}-${last} of ${total}`
}
