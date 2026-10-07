// What the first-run card says. Pure (no React, no browser), so all of it is tested here. What is promised:
// - nothing is shown while the search model is ready or still warming up, and nothing is shown before the first answer from the backend;
// - each problem the backend names has its own card; a download in progress wins over the problem it is fixing;
// - a failed download shows the reason and offers to try again; a download that "finished" but did not fix anything offers the download again, never a loop of nothing;
// - sizes and percentages are written for people.
import test from 'node:test'
import assert from 'node:assert/strict'
import { formatBytes, pullPercent, pullSummary, setupView } from './setup.ts'
import type { Health, PullStatus } from '../api/types.ts'

const health = (embedder: string, problem: string | null = null, detail: string | null = null): Health => ({ status: 'ok', embedder, model: embedder === 'ready' ? 'm@1' : null, detail, problem })
const pull = (state: PullStatus['state'], more: Partial<PullStatus> = {}): PullStatus => ({ state, model: 'qwen3-embedding:0.6b', message: null, percent: null, completed: null, total: null, error: null, ...more })

test('nothing is shown before the backend has answered, while it is ready, or while it is warming', () => {
  assert.deepEqual(setupView(null, null), { kind: 'none' })
  assert.deepEqual(setupView(health('ready'), null), { kind: 'none' })
  assert.deepEqual(setupView(health('warming'), null), { kind: 'none' })
  assert.deepEqual(setupView(health('ready'), pull('failed', { error: 'x' })), { kind: 'none' }, 'a working setup hides an old failed download')
})

test('Ollama not running has its own card', () => {
  assert.deepEqual(setupView(health('degraded', 'ollama_unavailable'), null), { kind: 'ollama_down' })
  assert.deepEqual(setupView(health('degraded', 'ollama_unavailable'), pull('idle')), { kind: 'ollama_down' })
})

test('a missing model offers the download', () => {
  assert.deepEqual(setupView(health('degraded', 'model_not_found'), null), { kind: 'model_missing' })
  assert.deepEqual(setupView(health('degraded', 'model_not_found'), pull('idle')), { kind: 'model_missing' })
})

test('a download in progress shows its progress, and wins over the problem it is fixing', () => {
  const view = setupView(health('degraded', 'model_not_found'), pull('pulling', { percent: 37, message: 'pulling abc', completed: 237, total: 640 }))
  assert.deepEqual(view, { kind: 'pulling', percent: 37, message: 'pulling abc', summary: '237 B of 640 B' })
  assert.equal(setupView(health('degraded', 'ollama_unavailable'), pull('pulling')).kind, 'pulling')
})

test('a download that has just started has no percent yet', () => {
  const view = setupView(health('degraded', 'model_not_found'), pull('pulling'))
  assert.deepEqual(view, { kind: 'pulling', percent: null, message: null, summary: null })
})

test('a failed download shows why and can be tried again', () => {
  assert.deepEqual(setupView(health('degraded', 'model_not_found'), pull('failed', { error: 'no space left on device' })), { kind: 'pull_failed', error: 'no space left on device' })
})

test('a failed download with no reason still says something', () => {
  const view = setupView(health('degraded', 'model_not_found'), pull('failed'))
  assert.equal(view.kind, 'pull_failed')
  assert.ok(view.kind === 'pull_failed' && view.error.length > 10)
})

test('a download that finished but did not fix the problem offers the download again', () => {
  assert.deepEqual(setupView(health('degraded', 'model_not_found'), pull('done')), { kind: 'model_missing' })
})

test('any other problem shows the backend sentence and a way to check again', () => {
  assert.deepEqual(setupView(health('degraded', 'embedding_error', 'Ollama gave a garbled answer'), null), { kind: 'other', detail: 'Ollama gave a garbled answer' })
  const view = setupView(health('degraded', 'embedding_error', null), null)
  assert.ok(view.kind === 'other' && view.detail.length > 10)
})

test('a degraded backend that names no problem (an older backend) is treated as the general problem', () => {
  assert.equal(setupView(health('degraded', null, 'something'), null).kind, 'other')
})

test('the percent is a whole number or unknown, and a bar never overflows', () => {
  assert.equal(pullPercent(pull('pulling', { percent: 37 })), 37)
  assert.equal(pullPercent(pull('pulling', { percent: 0 })), 0)
  assert.equal(pullPercent(pull('pulling', { percent: null })), null)
  assert.equal(pullPercent(pull('pulling', { percent: 140 })), 100)
  assert.equal(pullPercent(pull('pulling', { percent: -5 })), 0)
  assert.equal(pullPercent(pull('pulling', { percent: 37.6 })), 38)
})

test('sizes are written in the unit a person reads', () => {
  assert.equal(formatBytes(0), '0 B')
  assert.equal(formatBytes(512), '512 B')
  assert.equal(formatBytes(1536), '1.5 KB')
  assert.equal(formatBytes(5 * 1024 * 1024), '5.0 MB')
  assert.equal(formatBytes(639 * 1024 * 1024), '639 MB')
  assert.equal(formatBytes(1.5 * 1024 ** 3), '1.5 GB')
})

test('the unit changes exactly at 1024', () => {
  assert.equal(formatBytes(1000), '1000 B')
  assert.equal(formatBytes(1023), '1023 B')
  assert.equal(formatBytes(1024), '1.0 KB')
})

test('the summary needs both numbers', () => {
  assert.equal(pullSummary(pull('pulling', { completed: 100, total: 200 })), '100 B of 200 B')
  assert.equal(pullSummary(pull('pulling', { completed: null, total: 200 })), null)
  assert.equal(pullSummary(pull('pulling', { completed: 100, total: null })), null)
  assert.equal(pullSummary(pull('pulling', { completed: 100, total: 0 })), null)
})
