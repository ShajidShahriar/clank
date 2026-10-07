// How the source viewer decides which lines to ask for and which to mark. Pure, so it is tested here. What is promised:
// - the viewer asks for a little context around the source's lines, never before line 1, never more than the backend allows (500 lines);
// - the source's own lines are always inside what is asked for (the context shrinks first);
// - the rows carry the real line numbers and mark exactly the source's lines;
// - a changed file is flagged in words.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { CONTEXT_LINES, MAX_VIEW_LINES, PAGE_LINES, describeShown, mergeViews, moreRange, referenceRows, requestRange, rowsFor, viewNotice } from './sourceView.ts'

test('context is added on both sides', () => {
  assert.deepEqual(requestRange(50, 60), { start: 50 - CONTEXT_LINES, end: 60 + CONTEXT_LINES })
})

test('the range never starts before line 1', () => {
  assert.deepEqual(requestRange(1, 5), { start: 1, end: 5 + CONTEXT_LINES })
  assert.deepEqual(requestRange(4, 5), { start: 1, end: 5 + CONTEXT_LINES })
})

test('a one line source gets context too', () => {
  assert.deepEqual(requestRange(30, 30), { start: 30 - CONTEXT_LINES, end: 30 + CONTEXT_LINES })
})

test('the range is never longer than the backend allows and always holds the source lines', () => {
  for (const [start, end] of [[1, 500], [20, 519], [20, 400], [100, 560], [1, 5000], [7, 7], [3, 480]]) {
    const range = requestRange(start, end)
    assert.ok(range.end - range.start + 1 <= MAX_VIEW_LINES, `${start}-${end} -> ${range.start}-${range.end}`)
    assert.ok(range.start >= 1 && range.start <= start)
    assert.ok(range.end >= Math.min(end, start + MAX_VIEW_LINES - 1), `${start}-${end}`)
  }
})

test('a source bigger than the limit is cut to its first lines', () => {
  assert.deepEqual(requestRange(10, 5000), { start: 10, end: 10 + MAX_VIEW_LINES - 1 })
})

test('rows have the real line numbers and mark exactly the source lines', () => {
  const view = { path: 'a.py', start_line: 8, end_line: 13, total_lines: 100, stale: false, lines: ['l8', 'l9', 'l10', 'l11', 'l12', 'l13'] }
  const rows = rowsFor(view, { start_line: 10, end_line: 11 })
  assert.deepEqual(rows.map((r) => r.n), [8, 9, 10, 11, 12, 13])
  assert.deepEqual(rows.map((r) => r.marked), [false, false, true, true, false, false])
  assert.deepEqual(rows.map((r) => r.text), ['l8', 'l9', 'l10', 'l11', 'l12', 'l13'])
})

test('a blank line keeps its row', () => {
  const rows = rowsFor({ path: 'a', start_line: 1, end_line: 2, total_lines: 2, stale: false, lines: ['', 'x'] }, { start_line: 1, end_line: 2 })
  assert.equal(rows.length, 2)
  assert.equal(rows[0].text, '')
})

test('a file cut short (the source ends past the end of the file) marks only the lines that exist', () => {
  const rows = rowsFor({ path: 'a', start_line: 9, end_line: 10, total_lines: 10, stale: false, lines: ['l9', 'l10'] }, { start_line: 9, end_line: 14 })
  assert.deepEqual(rows.map((r) => r.marked), [true, true])
})

test('a changed file is flagged in words, an unchanged one is not', () => {
  const base = { path: 'a', start_line: 1, end_line: 1, total_lines: 1, lines: ['x'] }
  assert.equal(viewNotice({ ...base, stale: false }), null)
  assert.match(viewNotice({ ...base, stale: true }) ?? '', /changed after it was indexed/)
})

test('the limit here is the limit the backend enforces (the two files are read and compared)', () => {
  const python = readFileSync(new URL('../../backend/source_view.py', import.meta.url), 'utf8')
  const match = /^MAX_VIEW_LINES = (\d+)/m.exec(python)
  assert.ok(match, 'MAX_VIEW_LINES not found in backend/source_view.py')
  assert.equal(MAX_VIEW_LINES, Number(match[1]))
})

// ---- reading more of the file

const lines = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, i) => `l${from + i}`)
const part = (from: number, to: number, total = 1000, stale = false) => ({ path: 'a.py', start_line: from, end_line: to, total_lines: total, stale, lines: lines(from, to) })

test('the next range above is one page before what is shown, and stops at line 1', () => {
  assert.deepEqual(moreRange(part(300, 320), 'before'), { start: 300 - PAGE_LINES, end: 299 })
  assert.deepEqual(moreRange(part(40, 60), 'before'), { start: 1, end: 39 })
  assert.equal(moreRange(part(1, 20), 'before'), null)
})

test('the next range below is one page after what is shown, and stops at the last line', () => {
  assert.deepEqual(moreRange(part(300, 320, 1000), 'after'), { start: 321, end: 320 + PAGE_LINES })
  assert.deepEqual(moreRange(part(900, 960, 1000), 'after'), { start: 961, end: 1000 })
  assert.equal(moreRange(part(980, 1000, 1000), 'after'), null)
})

test('a page is never bigger than the backend allows', () => {
  assert.ok(PAGE_LINES >= 1 && PAGE_LINES <= MAX_VIEW_LINES)
})

test('lines that come before are put in front, in order, with no gap and no repeat', () => {
  const merged = mergeViews(part(50, 60), part(30, 49))
  assert.equal(merged.start_line, 30)
  assert.equal(merged.end_line, 60)
  assert.deepEqual(merged.lines, lines(30, 60))
})

test('lines that come after are put behind, in order, with no gap and no repeat', () => {
  const merged = mergeViews(part(50, 60), part(61, 80))
  assert.equal(merged.start_line, 50)
  assert.equal(merged.end_line, 80)
  assert.deepEqual(merged.lines, lines(50, 80))
})

test('rows of a merged view keep their real line numbers', () => {
  const merged = mergeViews(mergeViews(part(50, 52), part(47, 49)), part(53, 54))
  assert.deepEqual(rowsFor(merged, { start_line: 50, end_line: 51 }).map((r) => [r.n, r.text, r.marked]),
    [[47, 'l47', false], [48, 'l48', false], [49, 'l49', false], [50, 'l50', true], [51, 'l51', true], [52, 'l52', false], [53, 'l53', false], [54, 'l54', false]])
})

test('a part that does not touch what is shown is ignored, so a gap or a repeat can never happen', () => {
  const current = part(50, 60)
  for (const other of [part(10, 20), part(62, 70), part(40, 50), part(60, 70), part(52, 58), part(50, 60)]) {
    assert.deepEqual(mergeViews(current, other), current, `${other.start_line}-${other.end_line}`)
  }
})

test('a merge keeps the newest line count and remembers that the file changed', () => {
  assert.equal(mergeViews(part(50, 60, 1000), part(61, 70, 1004)).total_lines, 1004)
  assert.equal(mergeViews(part(50, 60, 1000, false), part(61, 70, 1000, true)).stale, true)
  assert.equal(mergeViews(part(50, 60, 1000, true), part(61, 70, 1000, false)).stale, true)
  assert.equal(mergeViews(part(50, 60, 1000, false), part(61, 70, 1000, false)).stale, false)
})

// ---- the reference only, or the whole file

test('the reference view keeps only the marked rows, in order, with their real numbers', () => {
  const rows = rowsFor(part(8, 14), { start_line: 10, end_line: 12 })
  assert.deepEqual(referenceRows(rows).map((r) => [r.n, r.text]), [[10, 'l10'], [11, 'l11'], [12, 'l12']])
})

test('the reference view of a source with no line on screen is empty', () => {
  assert.deepEqual(referenceRows(rowsFor(part(8, 14), { start_line: 40, end_line: 41 })), [])
})

test('the summary names the lines shown and the size of the file', () => {
  const rows = rowsFor(part(8, 14, 342), { start_line: 10, end_line: 12 })
  assert.equal(describeShown(referenceRows(rows), 342), 'Lines 10-12 of 342')
  assert.equal(describeShown(rows, 342), 'Lines 8-14 of 342')
  assert.equal(describeShown([rows[2]], 342), 'Line 10 of 342')
  assert.equal(describeShown([], 342), 'No lines to show')
})

test('the summary uses thousands separators like the rest of the window', () => {
  assert.equal(describeShown(rowsFor(part(1, 3, 12345), { start_line: 1, end_line: 3 }), 12345), 'Lines 1-3 of 12,345')
})
