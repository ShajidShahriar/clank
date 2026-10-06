// How the window turns backend data into words and pieces to draw. All pure, so all tested here; the React components only draw what these return.
import test from 'node:test'
import assert from 'node:assert/strict'
import { answerWarnings, describeError, progressFraction, projectStatus, sourceLabel, splitAnswer, tokenSummary } from './present.ts'
import type { Answer, IndexStatus, Project, Source } from '../api/types.ts'

const project: Project = { id: 1, name: 'p', path: '/p', created_at: 'x', indexed: false, files: 0, chunks: 0, flagged_files: 0, index_state: 'idle' }
const source: Source = { path: 'src/a.py', symbol: 'f', parent: null, kind: 'function', start_line: 10, end_line: 20, score: 0.7, stale: false, complete: true, narrowed: false }

// ---- splitAnswer

test('plain text is one text part', () => {
  assert.deepEqual(splitAnswer('It works by calling f.'), [{ kind: 'text', text: 'It works by calling f.', lang: '' }])
})

test('an empty or blank answer has no parts', () => {
  assert.deepEqual(splitAnswer(''), [])
  assert.deepEqual(splitAnswer('  \n\n '), [])
})

test('a fenced block becomes a code part with its language', () => {
  assert.deepEqual(splitAnswer('Look:\n```python\nx = 1\nprint(x)\n```\nThat is all.'), [
    { kind: 'text', text: 'Look:', lang: '' },
    { kind: 'code', text: 'x = 1\nprint(x)', lang: 'python' },
    { kind: 'text', text: 'That is all.', lang: '' },
  ])
})

test('a fence without a language, and two blocks in a row', () => {
  assert.deepEqual(splitAnswer('```\na\n```\n```js\nb\n```'), [
    { kind: 'code', text: 'a', lang: '' },
    { kind: 'code', text: 'b', lang: 'js' },
  ])
})

test('a fence that is never closed makes the rest code (an answer cut off in the middle of a block)', () => {
  assert.deepEqual(splitAnswer('Here:\n```python\ndef f():\n    return 1'), [
    { kind: 'text', text: 'Here:', lang: '' },
    { kind: 'code', text: 'def f():\n    return 1', lang: 'python' },
  ])
})

test('inline backticks stay in the text and are not fences', () => {
  assert.deepEqual(splitAnswer('Call `f()` then `g()`.'), [{ kind: 'text', text: 'Call `f()` then `g()`.', lang: '' }])
})

test('indentation inside code is kept, and blank lines inside text are kept', () => {
  const parts = splitAnswer('One.\n\nTwo.\n```\n  indented\n\n    more\n```')
  assert.deepEqual(parts[0], { kind: 'text', text: 'One.\n\nTwo.', lang: '' })
  assert.deepEqual(parts[1], { kind: 'code', text: '  indented\n\n    more', lang: '' })
})

test('the parts joined again hold all the words of the answer (nothing is lost)', () => {
  const text = 'A.\n```ts\nconst x = 1\n```\nB.\n```\ny\n```\nC.'
  const words = splitAnswer(text).map((p) => p.text).join(' ').replace(/\s+/g, ' ')
  for (const word of ['A.', 'const x = 1', 'B.', 'y', 'C.']) assert.ok(words.includes(word), word)
})

// ---- labels

test('a source is labelled path:start-end with a plain hyphen, or path:line for one line', () => {
  assert.equal(sourceLabel(source), 'src/a.py:10-20')
  assert.equal(sourceLabel({ ...source, start_line: 7, end_line: 7 }), 'src/a.py:7')
  assert.equal(sourceLabel(source).includes('‑'), false)
})

test('token counts are shown with thousands separators, and an unknown count is left out', () => {
  assert.equal(tokenSummary({ prompt_tokens: 1705, completion_tokens: 187 }), '1,705 in · 187 out')
  assert.equal(tokenSummary({ prompt_tokens: 1705, completion_tokens: null }), '1,705 in')
  assert.equal(tokenSummary({ prompt_tokens: null, completion_tokens: null }), '')
  assert.equal(tokenSummary(null), '')
})

test('the warnings under an answer', () => {
  const base = { sent_off_machine: false, truncated: false, stale_files: [], deleted_files: [], hidden_files: [], notice: null } as unknown as Answer
  assert.deepEqual(answerWarnings(base), [])
  assert.deepEqual(answerWarnings({ ...base, sent_off_machine: true }), ['Code excerpts were sent to a remote model.'])
  assert.deepEqual(answerWarnings({ ...base, truncated: true, notice: 'The answer was cut off by the length limit.' }), ['The answer was cut off by the length limit.'])
  assert.deepEqual(answerWarnings({ ...base, stale_files: ['a.py', 'b.py'] }), ['2 files changed since they were indexed, so some sources may be out of date.'])
  assert.deepEqual(answerWarnings({ ...base, stale_files: ['a.py'] }), ['1 file changed since it was indexed, so some sources may be out of date.'])
  assert.deepEqual(answerWarnings({ ...base, deleted_files: ['gone.py'] }), ['1 file no longer exists and was left out.'])
  assert.deepEqual(answerWarnings({ ...base, hidden_files: [{ path: 'x', reason: 'r' }, { path: 'y', reason: 'r' }] }), ['2 files could not be indexed and were left out.'])
})

// ---- project status

test('an index that is running shows its progress', () => {
  const running: IndexStatus = { state: 'running', project_id: 1, files_done: 12, files_total: 40 }
  assert.deepEqual(projectStatus(project, running), { label: 'Indexing 12/40', tone: 'busy' })
  assert.deepEqual(projectStatus(project, { ...running, files_done: 0, files_total: 0 }), { label: 'Indexing…', tone: 'busy' })
  assert.deepEqual(projectStatus(project, { ...running, state: 'cancelling' }), { label: 'Stopping…', tone: 'busy' })
})

test('an index that stopped, failed or was cancelled is a warning', () => {
  for (const [state, label] of [['stopped', 'Index stopped'], ['failed', 'Index failed'], ['cancelled', 'Index cancelled']] as const) {
    assert.deepEqual(projectStatus(project, { state, project_id: 1 }), { label, tone: 'warn' })
  }
})

test('an indexed project shows its file count, and skipped files', () => {
  assert.deepEqual(projectStatus({ ...project, indexed: true, files: 212 }), { label: '212 files', tone: 'ok' })
  assert.deepEqual(projectStatus({ ...project, indexed: true, files: 1 }), { label: '1 file', tone: 'ok' })
  assert.deepEqual(projectStatus({ ...project, indexed: true, files: 212, flagged_files: 3 }), { label: '212 files · 3 skipped', tone: 'ok' })
  assert.deepEqual(projectStatus({ ...project, indexed: true, files: 5 }, { state: 'done', project_id: 1 }), { label: '5 files', tone: 'ok' })
})

test('a project nobody indexed says so', () => {
  assert.deepEqual(projectStatus(project), { label: 'Not indexed', tone: 'neutral' })
  assert.deepEqual(projectStatus(project, { state: 'idle', project_id: 1 }), { label: 'Not indexed', tone: 'neutral' })
})

test('with no live status the project\'s own state from the list is used', () => {
  assert.deepEqual(projectStatus({ ...project, index_state: 'running' }), { label: 'Indexing…', tone: 'busy' })
  assert.deepEqual(projectStatus({ ...project, index_state: 'stopped' }), { label: 'Index stopped', tone: 'warn' })
})

test('a running index wins over an old indexed count (re-indexing)', () => {
  assert.equal(projectStatus({ ...project, indexed: true, files: 212 }, { state: 'running', project_id: 1, files_done: 1, files_total: 9 }).label, 'Indexing 1/9')
})

test('progress is a fraction between 0 and 1, or null when it is not known', () => {
  assert.equal(progressFraction({ state: 'running', project_id: 1, files_done: 10, files_total: 40 }), 0.25)
  assert.equal(progressFraction({ state: 'running', project_id: 1, files_done: 50, files_total: 40 }), 1)
  assert.equal(progressFraction({ state: 'running', project_id: 1, files_done: -3, files_total: 40 }), 0)
  assert.equal(progressFraction({ state: 'running', project_id: 1, files_done: 0, files_total: 0 }), null)
  assert.equal(progressFraction({ state: 'done', project_id: 1, files_done: 40, files_total: 40 }), null)
  assert.equal(progressFraction(undefined), null)
})

// ---- errors

const err = (code: string, message = 'Words for people.') => ({ code, message, status: 400 })

test('every error keeps the backend\'s own words', () => {
  for (const code of ['consent_required', 'llm_rate_limited', 'not_indexed', 'anything_new']) {
    assert.equal(describeError(err(code, `msg for ${code}`)).message, `msg for ${code}`)
  }
})

test('each error says what the person can do next', () => {
  const action = (code: string) => describeError(err(code)).action
  assert.equal(action('consent_required'), 'allow_remote')
  assert.equal(action('not_indexed'), 'index')
  assert.equal(action('index_out_of_date'), 'index')
  assert.equal(action('backend_unavailable'), 'restart_backend')
  assert.equal(action('project_not_found'), 'refresh')
  for (const code of ['ollama_unavailable', 'llm_unavailable', 'llm_timeout', 'backend_timeout', 'unknown_error', 'bad_response', 'llm_bad_response', 'embedding_error', 'llm_error', 'llm_context_too_long']) {
    assert.equal(action(code), 'retry', code)
  }
  for (const code of ['llm_not_configured', 'llm_auth_failed', 'llm_model_not_found']) {
    assert.equal(action(code), 'settings', code)
  }
  for (const code of ['no_bridge', 'invalid_request', 'invalid_path', 'project_exists', 'project_busy', 'empty_question', 'model_not_found', 'invalid_settings', 'invalid_key',
    'secure_storage_unavailable', 'key_save_failed', 'settings_failed', 'dialog_failed', 'forbidden']) {
    assert.equal(action(code), 'none', code)
  }
  assert.equal(action('something_never_seen'), 'retry', 'an unknown error can at least be tried again')
})

test('a rate limit says how many seconds to wait', () => {
  const wait = (message: string) => describeError(err('llm_rate_limited', message))
  assert.deepEqual([wait('The answer service\'s rate limit was reached. Try again in about 8 seconds.').action, wait('The answer service\'s rate limit was reached. Try again in about 8 seconds.').retryAfterSeconds], ['wait', 8])
  assert.equal(wait('The answer service\'s rate limit was reached. Try again in about 1 second.').retryAfterSeconds, 1)
  assert.equal(wait('The answer service\'s rate limit was reached. Try again in a minute.').retryAfterSeconds, 60)
  assert.equal(wait('The answer service\'s rate limit was reached.').retryAfterSeconds, 60, 'unknown: a minute, never "now"')
  assert.equal(describeError(err('consent_required')).retryAfterSeconds, null)
})
