// How saved messages become the entries the window draws, and how conversations are named and keyed. What is promised:
// - a saved question and its answer become ONE entry in the same shape a live answer has, in the order they were written;
// - a saved answer that cannot be read (damaged or from a different version) becomes an error entry, never a crash and never a half-drawn answer;
// - a conversation without a title is called "New conversation";
// - the entry lists of two conversations (or of a draft) never share a key.
import test from 'node:test'
import assert from 'node:assert/strict'
import { conversationLabel, entriesFromMessages, entriesKey } from './conversation.ts'
import type { SavedMessage } from '../api/types.ts'

const meta = {
  truncated: false, finish_reason: 'stop', notice: null, llm_called: true, model: 'm', profile: 'groq', usage: { prompt_tokens: 10, completion_tokens: 5 },
  sent_off_machine: true, sources: [{ path: 'a.py', symbol: 'f', parent: null, kind: 'function', start_line: 1, end_line: 9, score: 0.7, stale: false, complete: true, narrowed: false }],
  dropped: [], hidden_files: [], stale_files: [], deleted_files: [], context_tokens_used: 100, context_budget: 2500, over_budget: false, best_score: 0.7, k: 10,
  ranking_note: null, calibration_note: null,
}
const user = (id: number, content: string): SavedMessage => ({ id, role: 'user', content, meta: null, created_at: 'now' })
const assistant = (id: number, content: string, m: Record<string, unknown> | null = meta): SavedMessage => ({ id, role: 'assistant', content, meta: m, created_at: 'now' })

test('a question and its answer become one done entry, in order', () => {
  const entries = entriesFromMessages([user(1, 'first?'), assistant(2, 'one'), user(3, 'second?'), assistant(4, 'two')])
  assert.deepEqual(entries.map((e) => [e.question, e.state, e.answer?.answer]), [['first?', 'done', 'one'], ['second?', 'done', 'two']])
  assert.equal(entries[0].answer?.sources[0].path, 'a.py')
  assert.notEqual(entries[0].id, entries[1].id)
})

test('no messages, no entries', () => {
  assert.deepEqual(entriesFromMessages([]), [])
})

test('an answer with damaged or missing extras is an error entry that says so', () => {
  for (const bad of [null, {}, { ...meta, sources: 'none' }, { ...meta, usage: 5 }]) {
    const [entry] = entriesFromMessages([user(1, 'q?'), assistant(2, 'text', bad)])
    assert.equal(entry.state, 'error')
    assert.equal(entry.question, 'q?')
    assert.equal(entry.answer, undefined)
    assert.equal(entry.error?.code, 'saved_answer_unreadable')
    assert.match(entry.error?.message ?? '', /could not be read/)
  }
})

test('a question with no saved answer is an error entry, and the next question is still paired right', () => {
  const entries = entriesFromMessages([user(1, 'lost?'), user(2, 'next?'), assistant(3, 'fine')])
  assert.deepEqual(entries.map((e) => [e.question, e.state]), [['lost?', 'error'], ['next?', 'done']])
  assert.equal(entries[0].error?.code, 'saved_answer_missing')
})

test('an answer with no question before it is not drawn', () => {
  assert.deepEqual(entriesFromMessages([assistant(1, 'orphan')]), [])
})

test('the extras of the answer are not mixed into the text', () => {
  const [entry] = entriesFromMessages([user(1, 'q'), assistant(2, 'the text')])
  assert.equal(entry.answer?.answer, 'the text')
  assert.equal(entry.answer?.model, 'm')
})

test('a conversation without a title is New conversation; a title is shown as it is', () => {
  assert.equal(conversationLabel({ title: null }), 'New conversation')
  assert.equal(conversationLabel({ title: '' }), 'New conversation')
  assert.equal(conversationLabel({ title: '   ' }), 'New conversation')
  assert.equal(conversationLabel({ title: 'how does it work?' }), 'how does it work?')
})

test('entry keys differ per project, per conversation and for a draft', () => {
  const keys = [entriesKey(1, null), entriesKey(2, null), entriesKey(1, 5), entriesKey(2, 5), entriesKey(1, 6)]
  assert.equal(new Set(keys).size, keys.length)
  assert.equal(entriesKey(1, 5), entriesKey(1, 5))
})

test('a last question with no saved answer is an error entry too', () => {
  const entries = entriesFromMessages([user(1, 'first?'), assistant(2, 'one'), user(3, 'lost at the end?')])
  assert.deepEqual(entries.map((e) => [e.question, e.state]), [['first?', 'done'], ['lost at the end?', 'error']])
  assert.equal(entries[1].error?.code, 'saved_answer_missing')
})

test('the saved text is the answer even when the extras carry an answer key of their own', () => {
  const [entry] = entriesFromMessages([user(1, 'q'), assistant(2, 'the real text', { ...meta, answer: 'wrong text' })])
  assert.equal(entry.answer?.answer, 'the real text')
})
