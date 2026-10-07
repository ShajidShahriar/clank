// Streaming answers, step 1.4, part 2: what the window shows while an answer is being written, and the summary line it keeps afterwards. Pure (no React, no clock of its own:
// the time is always passed in), so all of it is tested here. What is promised:
// - an entry follows the events: searching, then waiting for the model, then thinking (only if the model thinks), then writing; the text only ever grows by what arrived;
// - the live line says where it is and how long it has been there; it never invents a thinking stage for a model that does not think;
// - times are written for people (0.4 s, 3.2 s, 42 s, 1 min 5 s); tokens that are estimates say "about";
// - the summary line of a finished answer: "Searched 0.4 s · Thought 3.2 s, 412 tokens · Answer 268 tokens · 5.1 s total"; a model that does not think says "No thinking";
//   an answer saved before these numbers existed has no summary line;
// - a done event becomes the answer; an error keeps the half-written text and the error; a stop keeps the half-written text and says it was stopped.
import test from 'node:test'
import assert from 'node:assert/strict'
import { applyEvent, entryAfterEvent, formatSeconds, formatTokens, initialLive, stageLabel, startEntry, summaryLine } from './liveAnswer.ts'
import type { Answer, DoneEvent, StartEvent, StreamEvent } from '../api/types.ts'

const source = { path: 'a.py', symbol: 'f', parent: null, kind: 'function', start_line: 1, end_line: 9, score: 0.7, stale: false, complete: true, narrowed: false }
const context = { sources: [source], dropped: [], hidden_files: [], stale_files: [], deleted_files: [], context_tokens_used: 100, context_budget: 2500, over_budget: false, best_score: 0.7, k: 10, ranking_note: null, calibration_note: null }
const start: StartEvent = { type: 'start', stage: 'waiting', search_ms: 400, llm_called: true, profile: 'groq', sent_off_machine: true, conversation_id: null, ...context }
const done: DoneEvent = {
  type: 'done', answer: 'It works.', truncated: false, finish_reason: 'stop', notice: null, llm_called: true, model: 'm', profile: 'groq', usage: { prompt_tokens: 90, completion_tokens: 84 },
  sent_off_machine: true, ...context, timings: { search_ms: 400, wait_ms: 500, thinking_ms: 3200, writing_ms: 1000, total_ms: 5100 }, thinking: { seen: true, pieces: 412 },
  tokens: { thinking: 412, answer: 268, estimated: false }, conversation_id: null, saved: false,
}

// ---- time and tokens in words

test('seconds are written for people', () => {
  assert.equal(formatSeconds(0), '0.0 s')
  assert.equal(formatSeconds(400), '0.4 s')
  assert.equal(formatSeconds(3200), '3.2 s')
  assert.equal(formatSeconds(9949), '9.9 s')
  assert.equal(formatSeconds(9950), '10 s')
  assert.equal(formatSeconds(42_400), '42 s')
  assert.equal(formatSeconds(59_400), '59 s')
  assert.equal(formatSeconds(59_600), '1 min')
  assert.equal(formatSeconds(65_000), '1 min 5 s')
  assert.equal(formatSeconds(120_000), '2 min')
  assert.equal(formatSeconds(3_725_000), '62 min 5 s')
})

test('a time that cannot be right is shown as zero, never negative or odd', () => {
  for (const bad of [-5, NaN, Infinity, -Infinity]) assert.equal(formatSeconds(bad), '0.0 s')
})

test('tokens are written with separators, singular for one, and "about" for an estimate', () => {
  assert.equal(formatTokens(412, false), '412 tokens')
  assert.equal(formatTokens(1, false), '1 token')
  assert.equal(formatTokens(0, false), '0 tokens')
  assert.equal(formatTokens(12_345, false), '12,345 tokens')
  assert.equal(formatTokens(412, true), 'about 412 tokens')
  assert.equal(formatTokens(1, true), 'about 1 token')
})

// ---- following the events

test('a new entry is searching', () => {
  const live = initialLive(1000)
  assert.deepEqual(live, { stage: 'searching', stageStartedAt: 1000, startedAt: 1000, text: '', thinkingPieces: 0, start: null })
})

test('start means the search is done and the model is being waited for', () => {
  const live = applyEvent(initialLive(1000), start, 1400)
  assert.equal(live.stage, 'waiting')
  assert.equal(live.stageStartedAt, 1400)
  assert.equal(live.start, start)
  assert.equal(live.startedAt, 1000, 'the start of the whole question does not move')
})

test('the stages follow the stage events, and the time of a stage is when it began', () => {
  let live = applyEvent(initialLive(0), start, 400)
  live = applyEvent(live, { type: 'stage', stage: 'thinking', at_ms: 900 }, 1000)
  assert.deepEqual([live.stage, live.stageStartedAt], ['thinking', 1000])
  live = applyEvent(live, { type: 'stage', stage: 'writing', at_ms: 3000 }, 3100)
  assert.deepEqual([live.stage, live.stageStartedAt], ['writing', 3100])
})

test('thinking counts only go up', () => {
  let live = initialLive(0)
  live = applyEvent(live, { type: 'thinking', pieces: 5 }, 10)
  live = applyEvent(live, { type: 'thinking', pieces: 3 }, 20)
  assert.equal(live.thinkingPieces, 5, 'a lower count that arrives late does not move it back')
  live = applyEvent(live, { type: 'thinking', pieces: 9 }, 30)
  assert.equal(live.thinkingPieces, 9)
})

test('text grows by exactly what arrived, in order, and a delta means writing even if no stage event came', () => {
  let live = applyEvent(initialLive(0), start, 400)
  live = applyEvent(live, { type: 'delta', text: '  spaced ' }, 500)
  live = applyEvent(live, { type: 'delta', text: 'out\n\n' }, 600)
  live = applyEvent(live, { type: 'delta', text: 'é🙂' }, 700)
  assert.equal(live.text, '  spaced out\n\né🙂')
  assert.equal(live.stage, 'writing')
})

test('a delta in the writing stage does not restart the stage clock', () => {
  let live = applyEvent(initialLive(0), { type: 'stage', stage: 'writing', at_ms: 1 }, 1000)
  live = applyEvent(live, { type: 'delta', text: 'a' }, 1500)
  assert.equal(live.stageStartedAt, 1000)
})

test('a model that goes straight to writing has no thinking stage at any moment', () => {
  const stages: string[] = []
  let live = initialLive(0)
  for (const event of [start, { type: 'stage', stage: 'writing', at_ms: 500 }, { type: 'delta', text: 'a' }, { type: 'delta', text: 'b' }] as StreamEvent[]) {
    live = applyEvent(live, event, 1)
    stages.push(live.stage)
  }
  assert.equal(stages.includes('thinking'), false)
})

test('applying an event does not change the live state it was given', () => {
  const live = initialLive(0)
  const copy = JSON.parse(JSON.stringify(live))
  applyEvent(live, { type: 'delta', text: 'a' }, 5)
  assert.deepEqual(live, copy)
})

test('the last events (done, error, cancelled) are not the live state\'s business', () => {
  const live = initialLive(0)
  for (const event of [done, { type: 'error', error: { code: 'x', message: 'y', status: 0 } }, { type: 'cancelled' }] as StreamEvent[]) assert.equal(applyEvent(live, event, 5), live)
})

// ---- the live line

test('the live line says where the answer is and for how long', () => {
  const searching = initialLive(0)
  assert.equal(stageLabel(searching, 900), 'Searching the code…')
  const waiting = applyEvent(searching, start, 400)
  assert.equal(stageLabel(waiting, 1600), 'Waiting for the model… 1.2 s')
  const thinking = applyEvent(applyEvent(waiting, { type: 'stage', stage: 'thinking', at_ms: 1 }, 1000), { type: 'thinking', pieces: 180 }, 3400)
  assert.equal(stageLabel(thinking, 3400), 'Thinking… 2.4 s · about 180 tokens')
  const writing = applyEvent(thinking, { type: 'stage', stage: 'writing', at_ms: 1 }, 3500)
  assert.equal(stageLabel(writing, 9000), 'Writing…')
})

test('the thinking line has no token count before the first piece', () => {
  const live = applyEvent(initialLive(0), { type: 'stage', stage: 'thinking', at_ms: 1 }, 100)
  assert.equal(stageLabel(live, 200), 'Thinking… 0.1 s')
})

test('one token of thinking is singular', () => {
  const live = applyEvent(applyEvent(initialLive(0), { type: 'stage', stage: 'thinking', at_ms: 1 }, 0), { type: 'thinking', pieces: 1 }, 0)
  assert.equal(stageLabel(live, 100), 'Thinking… 0.1 s · about 1 token')
})

test('the clock running behind the stage start never shows a negative time', () => {
  const live = applyEvent(initialLive(0), start, 5000)
  assert.equal(stageLabel(live, 1000), 'Waiting for the model… 0.0 s')
})

// ---- the summary line of a finished answer

const answerOf = (over: Partial<DoneEvent> = {}): Answer => {
  const { type, saved, ...answer } = { ...done, ...over }
  void type; void saved
  return answer
}

test('the summary line has the search, the thinking, the answer and the total', () => {
  assert.equal(summaryLine(answerOf()), 'Searched 0.4 s · Thought 3.2 s, 412 tokens · Answer 268 tokens · 5.1 s total')
})

test('estimated tokens say "about"', () => {
  assert.equal(summaryLine(answerOf({ tokens: { thinking: 412, answer: 268, estimated: true } })), 'Searched 0.4 s · Thought 3.2 s, about 412 tokens · Answer about 268 tokens · 5.1 s total')
})

test('a model that does not think says so, and does not pretend to have thought', () => {
  const line = summaryLine(answerOf({ timings: { ...done.timings, thinking_ms: null }, thinking: { seen: false, pieces: 0 }, tokens: { thinking: null, answer: 38, estimated: false } }))
  assert.equal(line, 'Searched 0.4 s · No thinking · Answer 38 tokens · 5.1 s total')
})

test('thinking the service counted but did not stream has tokens and no time', () => {
  const line = summaryLine(answerOf({ timings: { ...done.timings, thinking_ms: null }, thinking: { seen: false, pieces: 0 }, tokens: { thinking: 300, answer: 38, estimated: false } }))
  assert.equal(line, 'Searched 0.4 s · Thought 300 tokens · Answer 38 tokens · 5.1 s total')
})

test('thinking with a time but no token count has just the time', () => {
  const line = summaryLine(answerOf({ tokens: { thinking: null, answer: 38, estimated: false } }))
  assert.equal(line, 'Searched 0.4 s · Thought 3.2 s · Answer 38 tokens · 5.1 s total')
})

test('an answer with no token counts at all leaves them out', () => {
  const line = summaryLine(answerOf({ tokens: { thinking: null, answer: null, estimated: false }, timings: { ...done.timings, thinking_ms: null }, thinking: { seen: false, pieces: 0 } }))
  assert.equal(line, 'Searched 0.4 s · No thinking · 5.1 s total')
})

test('an answer from the model that was never called has only the search', () => {
  const line = summaryLine(answerOf({ llm_called: false, timings: { search_ms: 300, wait_ms: null, thinking_ms: null, writing_ms: null, total_ms: 310 }, thinking: { seen: false, pieces: 0 }, tokens: { thinking: null, answer: null, estimated: false } }))
  assert.equal(line, 'Searched 0.3 s')
})

test('an answer saved before these numbers existed has no summary line', () => {
  const { timings, thinking, tokens, ...old } = answerOf()
  void timings; void thinking; void tokens
  assert.equal(summaryLine(old), null)
})

test('a summary line with only some of the numbers still reads well', () => {
  const line = summaryLine(answerOf({ timings: { search_ms: null, wait_ms: null, thinking_ms: null, writing_ms: null, total_ms: 5100 }, thinking: { seen: false, pieces: 0 }, tokens: { thinking: null, answer: 20, estimated: false } }))
  assert.equal(line, 'No thinking · Answer 20 tokens · 5.1 s total')
})

// ---- an entry following the events

test('a new entry is pending, searching, and remembers when it began', () => {
  assert.deepEqual(startEntry('q1', 'how?', 1000), { id: 'q1', question: 'how?', state: 'pending', live: initialLive(1000) })
})

test('the first event makes it streaming; the sources are known as soon as the search is done', () => {
  const entry = entryAfterEvent(startEntry('q1', 'how?', 0), start, 400)
  assert.equal(entry.state, 'streaming')
  assert.equal(entry.live?.start, start)
})

test('text events grow the text; the entry stays streaming', () => {
  let entry = entryAfterEvent(startEntry('q1', 'how?', 0), start, 400)
  entry = entryAfterEvent(entry, { type: 'delta', text: 'It ' }, 500)
  entry = entryAfterEvent(entry, { type: 'delta', text: 'works.' }, 600)
  assert.equal(entry.state, 'streaming')
  assert.equal(entry.live?.text, 'It works.')
})

test('done turns it into the answer, and drops the live state and the extras that are not part of an answer', () => {
  const entry = entryAfterEvent(entryAfterEvent(startEntry('q1', 'how?', 0), start, 400), done, 5000)
  assert.equal(entry.state, 'done')
  assert.equal(entry.live, undefined)
  assert.equal(entry.answer?.answer, 'It works.')
  assert.equal('type' in (entry.answer as object), false)
  assert.equal('saved' in (entry.answer as object), false)
  assert.deepEqual(entry.answer?.timings, done.timings)
  assert.equal(entry.id, 'q1')
  assert.equal(entry.question, 'how?')
})

test('an error keeps the half-written text and the error, and drops the live state', () => {
  let entry = entryAfterEvent(startEntry('q1', 'how?', 0), start, 400)
  entry = entryAfterEvent(entry, { type: 'delta', text: 'half an ans' }, 500)
  entry = entryAfterEvent(entry, { type: 'error', error: { code: 'llm_timeout', message: 'slow', status: 504 } }, 600)
  assert.equal(entry.state, 'error')
  assert.deepEqual(entry.error, { code: 'llm_timeout', message: 'slow', status: 504 })
  assert.equal(entry.partial, 'half an ans')
  assert.equal(entry.live, undefined)
})

test('an error before any text has no partial text', () => {
  const entry = entryAfterEvent(startEntry('q1', 'how?', 0), { type: 'error', error: { code: 'x', message: 'y', status: 0 } }, 100)
  assert.equal(entry.partial, undefined)
})

test('a rate limit keeps how long to wait', () => {
  const entry = entryAfterEvent(startEntry('q1', 'how?', 0), { type: 'error', error: { code: 'llm_rate_limited', message: 'Try again in about 8 seconds.', status: 429 }, retry_after: 8 }, 100)
  assert.equal(entry.retryAfter, 8)
})

test('cancelled keeps the half-written text and says it was stopped', () => {
  let entry = entryAfterEvent(startEntry('q1', 'how?', 0), start, 400)
  entry = entryAfterEvent(entry, { type: 'delta', text: 'half' }, 500)
  entry = entryAfterEvent(entry, { type: 'cancelled' }, 600)
  assert.equal(entry.state, 'stopped')
  assert.equal(entry.partial, 'half')
  assert.equal(entry.live, undefined)
})

test('an event for an entry that already ended changes nothing', () => {
  const ended = entryAfterEvent(startEntry('q1', 'how?', 0), done, 100)
  for (const event of [{ type: 'delta', text: 'late' }, { type: 'cancelled' }, { type: 'error', error: { code: 'x', message: 'y', status: 0 } }] as StreamEvent[]) assert.equal(entryAfterEvent(ended, event, 200), ended)
})

test('applying events does not change the entry it was given', () => {
  const entry = startEntry('q1', 'how?', 0)
  const copy = JSON.parse(JSON.stringify(entry))
  entryAfterEvent(entry, { type: 'delta', text: 'a' }, 5)
  assert.deepEqual(entry, copy)
})


test('a stage event for the stage the answer is already in does not restart its clock', () => {
  let live = applyEvent(initialLive(0), { type: 'stage', stage: 'thinking', at_ms: 1 }, 1000)
  live = applyEvent(live, { type: 'stage', stage: 'thinking', at_ms: 2 }, 5000)
  assert.equal(live.stageStartedAt, 1000)
  assert.equal(stageLabel(live, 6000), 'Thinking… 5.0 s')
})

test('a later stage event moves the stage and starts its clock', () => {
  let live = applyEvent(initialLive(0), { type: 'stage', stage: 'thinking', at_ms: 1 }, 1000)
  live = applyEvent(live, { type: 'stage', stage: 'writing', at_ms: 2 }, 5000)
  assert.deepEqual([live.stage, live.stageStartedAt], ['writing', 5000])
})
