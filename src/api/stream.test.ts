// Streaming answers, step 1.4, part 1: the window's way to ask for an answer written live (`api.askStream`). What is promised:
// - the listener is put in place BEFORE the start is sent (the first events can arrive before the start's own reply does, and must not be lost);
// - the question and the consent travel exactly as for a normal answer; the id is made here (so Stop works from the first moment); a blank question or a bad id is refused
//   without calling the bridge;
// - every event is checked: a bad one is never half-used, it becomes ONE `bad_response` error event, the stream is stopped, and nothing after it is passed on;
// - events of other streams are ignored; a stream ends with exactly one last event (done, error or cancelled) and nothing after it; the listener is taken off on EVERY way to end;
// - a start that is refused (no consent, not indexed, rate limit...) comes back as an ordinary error and no event ever follows;
// - a bridge that cannot stream says so (`canStream` is false), so the window can fall back to the one-shot answer.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createApi } from './client.ts'

const source = { path: 'a.py', symbol: 'f', parent: null, kind: 'function', start_line: 1, end_line: 9, score: 0.7, stale: false, complete: true, narrowed: false }
const context = { sources: [source], dropped: [], hidden_files: [], stale_files: [], deleted_files: [], context_tokens_used: 100, context_budget: 2500, over_budget: false, best_score: 0.7, k: 10, ranking_note: null, calibration_note: null }
const start = { type: 'start', stage: 'waiting', search_ms: 400, llm_called: true, profile: 'groq', sent_off_machine: true, conversation_id: null, ...context }
const done = {
  type: 'done', answer: 'It works.', truncated: false, finish_reason: 'stop', notice: null, llm_called: true, model: 'm', profile: 'groq', usage: { prompt_tokens: 90, completion_tokens: 84 },
  sent_off_machine: true, ...context, timings: { search_ms: 400, wait_ms: 500, thinking_ms: 1250, writing_ms: 350, total_ms: 2500 }, thinking: { seen: true, pieces: 54 },
  tokens: { thinking: 54, answer: 30, estimated: false }, conversation_id: null, saved: false,
}

function harness(extra: Record<string, unknown> = {}) {
  let listener: ((message: unknown) => void) | null = null
  const log: string[] = []
  const starts: Array<{ id: string, path: string, body?: object }> = []
  const stops: string[] = []
  let unsubscribed = 0
  let release: (reply: unknown) => void = () => {}
  const bridge = {
    request: async () => ({ ok: true, status: 200, body: {} }),
    pickFolder: async () => ({ ok: true, status: 200, body: { path: null } }),
    saveLlmSettings: async () => ({ ok: true, status: 200, body: {} }),
    startStream: (id: string, path: string, body?: object) => {
      log.push('start')
      starts.push({ id, path, body })
      return new Promise((resolve) => { release = resolve })
    },
    stopStream: async (id: string) => { stops.push(id); return { ok: true, status: 200, body: { stopped: true } } },
    onStreamEvents: (fn: (message: unknown) => void) => { log.push('listen'); listener = fn; return () => { unsubscribed++; listener = null } },
    ...extra,
  }
  const events: any[] = []
  const api = createApi(bridge as never)
  return {
    api, bridge, log, starts, stops, events, unsubscribed: () => unsubscribed, listening: () => listener !== null,
    emit: (message: unknown) => listener?.(message),
    accept: () => release({ ok: true, status: 200, body: { id: starts[0].id } }),
    refuse: (status: number, error: { code: string, message: string }) => release({ ok: false, status, body: { error } }),
    ask: (over: Record<string, unknown> = {}) => api.askStream({ projectId: 3, question: 'how does it work?', allowRemote: true, onEvent: (e: unknown) => events.push(e), ...over } as never),
  }
}

const tick = () => new Promise((resolve) => setImmediate(resolve))

test('the listener is in place before the start is sent', () => {
  const h = harness()
  h.ask()
  assert.deepEqual(h.log, ['listen', 'start'])
})

test('the start carries the path, the question and the consent, with an id the window made', () => {
  const h = harness()
  const stream = h.ask({ conversationId: 7 })
  assert.equal(h.starts.length, 1)
  assert.equal(h.starts[0].path, '/projects/3/answer/stream')
  assert.deepEqual(h.starts[0].body, { question: 'how does it work?', allow_remote: true, conversation_id: 7 })
  assert.match(h.starts[0].id, /^[A-Za-z0-9_-]{8,64}$/)
  assert.equal(stream.id, h.starts[0].id)
})

test('no conversation means no conversation id, and k is sent only when given', () => {
  const h = harness()
  h.ask()
  h.ask({ k: 5 })
  assert.deepEqual(h.starts[0].body, { question: 'how does it work?', allow_remote: true })
  assert.deepEqual(h.starts[1].body, { question: 'how does it work?', allow_remote: true, k: 5 })
})

test('two streams get two different ids', () => {
  const h = harness()
  h.ask()
  h.ask()
  assert.notEqual(h.starts[0].id, h.starts[1].id)
})

test('a blank question, a bad project or a bad conversation id is refused without calling the bridge', async () => {
  const h = harness()
  for (const over of [{ question: '' }, { question: '   ' }, { projectId: 0 }, { projectId: 1.5 }, { projectId: '3' }, { conversationId: 0 }, { conversationId: -2 }, { conversationId: '7' }]) {
    const stream = h.ask(over)
    const end = await stream.finished
    assert.equal(end.ok, false, JSON.stringify(over))
    assert.ok(['empty_question', 'bad_request'].includes((end as { error: { code: string } }).error.code), JSON.stringify(over))
  }
  assert.equal(h.starts.length, 0)
  assert.equal(h.log.length, 0)
})

// ---- events

test('events arrive in order and the stream ends with done', async () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: stream.id, events: [start, { type: 'stage', stage: 'thinking', at_ms: 900 }, { type: 'thinking', pieces: 3 }] })
  h.emit({ id: stream.id, events: [{ type: 'stage', stage: 'writing', at_ms: 2000 }, { type: 'delta', text: 'It ' }, { type: 'delta', text: 'works.' }, done] })
  assert.deepEqual(h.events.map((e) => e.type), ['start', 'stage', 'thinking', 'stage', 'delta', 'delta', 'done'])
  h.accept()
  assert.deepEqual(await stream.finished, { ok: true })
  assert.equal(h.unsubscribed(), 1)
})

test('events that arrive before the start has been answered are not lost', async () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: stream.id, events: [start] })                 // the main process starts reading as soon as the backend has answered: this can come first
  assert.deepEqual(h.events.map((e) => e.type), ['start'])
  h.accept()
  await tick()
  h.emit({ id: stream.id, events: [done] })
  assert.deepEqual(await stream.finished, { ok: true })
})

test('events of other streams are ignored', () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: 'some-other-stream', events: [start, done] })
  h.emit({ id: stream.id, events: [{ type: 'delta', text: 'mine' }] })
  assert.deepEqual(h.events, [{ type: 'delta', text: 'mine' }])
  assert.equal(h.listening(), true)
})

test('nothing after the last event is passed on, and the listener is off', async () => {
  const h = harness()
  const stream = h.ask()
  h.accept()
  h.emit({ id: stream.id, events: [done, { type: 'delta', text: 'late' }] })
  h.emit({ id: stream.id, events: [{ type: 'delta', text: 'later' }] })
  assert.deepEqual(h.events.map((e) => e.type), ['done'])
  assert.equal(h.unsubscribed(), 1)
  assert.deepEqual(await stream.finished, { ok: true })
})

for (const type of ['error', 'cancelled']) {
  test(`${type} ends the stream the same way`, async () => {
    const h = harness()
    const stream = h.ask()
    h.accept()
    h.emit({ id: stream.id, events: [{ type: 'delta', text: 'half' }, type === 'error' ? { type, error: { code: 'llm_timeout', message: 'slow', status: 504 } } : { type }] })
    h.emit({ id: stream.id, events: [{ type: 'delta', text: 'late' }] })
    assert.deepEqual(h.events.map((e) => e.type), ['delta', type])
    assert.equal(h.unsubscribed(), 1)
    assert.deepEqual(await stream.finished, { ok: true })
  })
}

test('an error event keeps the backend sentence and the wait for a rate limit', () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: stream.id, events: [{ type: 'error', error: { code: 'llm_rate_limited', message: 'Try again in about 8 seconds.', status: 429 }, retry_after: 8 }] })
  assert.deepEqual(h.events[0], { type: 'error', error: { code: 'llm_rate_limited', message: 'Try again in about 8 seconds.', status: 429 }, retry_after: 8 })
})

// ---- every event is checked

const bad: Array<[string, unknown]> = [
  ['not an object', 'text'], ['an array', []], ['no type', { text: 'x' }], ['an unknown type', { type: 'mystery' }], ['a type that is not text', { type: 5 }],
  ['a start without a source list', { ...start, sources: 'none' }], ['a start with a wrong stage', { ...start, stage: 'writing' }], ['a start without the search time', { ...start, search_ms: null }],
  ['a stage that is unknown', { type: 'stage', stage: 'dreaming', at_ms: 1 }], ['a stage without a time', { type: 'stage', stage: 'writing' }], ['a stage called waiting', { type: 'stage', stage: 'waiting', at_ms: 1 }],
  ['a thinking count that is text', { type: 'thinking', pieces: '3' }], ['a negative thinking count', { type: 'thinking', pieces: -1 }],
  ['a delta whose text is a number', { type: 'delta', text: 5 }], ['a delta with no text', { type: 'delta' }],
  ['a done with no timings', { ...done, timings: undefined }], ['a done with timings of the wrong type', { ...done, timings: { ...done.timings, wait_ms: 'x' } }],
  ['a done with no thinking info', { ...done, thinking: undefined }], ['a done with no token info', { ...done, tokens: undefined }], ['a done with token info of the wrong type', { ...done, tokens: { thinking: 1, answer: 2 } }],
  ['a done that is not an answer', { type: 'done', answer: 5 }], ['a done without saved', { ...done, saved: undefined }],
  ['a start whose llm_called is not a boolean', { ...start, llm_called: 'yes' }], ['a start whose profile is not text', { ...start, profile: 5 }],
  ['a start whose sent_off_machine is missing', { ...start, sent_off_machine: undefined }], ['a start whose conversation id is text', { ...start, conversation_id: 'x' }],
  ['a done whose conversation id is text', { ...done, conversation_id: 'x' }], ['a done whose answer is a number but whose extras are fine', { ...done, answer: 5 }],
  ['a done whose thinking info lacks seen', { ...done, thinking: { pieces: 1 } }], ['a done whose thinking count is negative', { ...done, thinking: { seen: true, pieces: -1 } }],
  ['a done whose token count is negative', { ...done, tokens: { thinking: -1, answer: 2, estimated: false } }],
  ['an error without a message', { type: 'error', error: { code: 'c', status: 1 } }], ['an error without a status', { type: 'error', error: { code: 'c', message: 'm' } }],
  ['an error without a code', { type: 'error', error: { message: 'x', status: 1 } }], ['an error that is text', { type: 'error', error: 'x' }], ['an error with a bad wait', { type: 'error', error: { code: 'c', message: 'm', status: 1 }, retry_after: 'soon' }],
]

for (const [name, event] of bad) {
  test(`${name} becomes one bad_response error, the stream is stopped and what follows is not passed on`, async () => {
    const h = harness()
    const stream = h.ask()
    h.accept()
    h.emit({ id: stream.id, events: [{ type: 'delta', text: 'good' }, event, { type: 'delta', text: 'never' }] })
    h.emit({ id: stream.id, events: [done] })
    assert.deepEqual(h.events.map((e) => e.type), ['delta', 'error'])
    assert.equal(h.events[1].error.code, 'bad_response')
    assert.deepEqual(h.stops, [stream.id], 'the backend is told to stop')
    assert.equal(h.unsubscribed(), 1)
    assert.deepEqual(await stream.finished, { ok: true })
  })
}

test('a message that is not {id, events} is ignored', () => {
  const h = harness()
  h.ask()
  for (const message of [null, 'x', 5, [], {}, { id: 5, events: [] }, { id: 'x', events: 'no' }]) h.emit(message)
  assert.equal(h.events.length, 0)
  assert.equal(h.listening(), true)
})

// ---- a start that is refused

test('a refused start comes back as an ordinary error and no event ever follows', async () => {
  const h = harness()
  const stream = h.ask()
  h.refuse(403, { code: 'consent_required', message: 'The answer model is a remote service.' })
  assert.deepEqual(await stream.finished, { ok: false, error: { code: 'consent_required', message: 'The answer model is a remote service.', status: 403 } })
  h.emit({ id: stream.id, events: [done] })
  assert.equal(h.events.length, 0)
  assert.equal(h.unsubscribed(), 1)
})

test('a reply to the start that is not a reply is a bad_response', async () => {
  const h = harness({ startStream: async () => 'nonsense' })
  const stream = h.ask()
  const end = await stream.finished as { ok: boolean, error: { code: string } }
  assert.equal(end.ok, false)
  assert.equal(end.error.code, 'bad_response')
  assert.equal(h.unsubscribed(), 1)
})

test('a bridge that throws is backend_unavailable, with no detail', async () => {
  const h = harness({ startStream: async () => { throw new Error('secret detail at port 4321') } })
  const end = await h.ask().finished as { ok: boolean, error: { code: string, message: string } }
  assert.equal(end.error.code, 'backend_unavailable')
  assert.equal(JSON.stringify(end).includes('secret'), false)
  assert.equal(h.unsubscribed(), 1)
})

test('a refusal that is stopped while it was waiting does not leave a listener behind', async () => {
  const h = harness()
  const stream = h.ask()
  await stream.stop()
  h.refuse(0, { code: 'cancelled', message: 'The answer was stopped.' })
  await stream.finished
  assert.equal(h.unsubscribed(), 1)
})

// ---- stopping

test('stop tells the main process which stream to stop', async () => {
  const h = harness()
  const stream = h.ask()
  h.accept()
  await stream.stop()
  assert.deepEqual(h.stops, [stream.id])
})

test('the cancelled event that follows a stop ends the stream', async () => {
  const h = harness()
  const stream = h.ask()
  h.accept()
  await stream.stop()
  h.emit({ id: stream.id, events: [{ type: 'cancelled' }] })
  assert.deepEqual(h.events.map((e) => e.type), ['cancelled'])
  assert.equal(h.unsubscribed(), 1)
})

test('stopping twice, or after the end, does nothing more', async () => {
  const h = harness()
  const stream = h.ask()
  h.accept()
  h.emit({ id: stream.id, events: [done] })
  await stream.stop()
  await stream.stop()
  assert.deepEqual(h.stops, [], 'a finished stream has nothing to stop')
})

test('a stop that fails does not throw', async () => {
  const h = harness({ stopStream: async () => { throw new Error('gone') } })
  const stream = h.ask()
  await assert.doesNotReject(stream.stop())
})

test('a stop sent twice before the end is sent once', async () => {
  const h = harness()
  const stream = h.ask()
  await Promise.all([stream.stop(), stream.stop()])
  assert.equal(h.stops.length, 1)
})

// ---- a bridge that cannot stream

test('canStream says whether the bridge has the three doors', () => {
  assert.equal(harness().api.canStream, true)
  assert.equal(harness({ startStream: undefined }).api.canStream, false)
  assert.equal(harness({ stopStream: undefined }).api.canStream, false)
  assert.equal(harness({ onStreamEvents: undefined }).api.canStream, false)
  assert.equal(createApi(undefined).canStream, false)
})

test('asking to stream with no way to stream is an error, not a crash', async () => {
  const h = harness({ startStream: undefined })
  const end = await h.ask().finished as { ok: boolean, error: { code: string } }
  assert.equal(end.ok, false)
  assert.equal(end.error.code, 'stream_unsupported')
  assert.equal(createApi(undefined).askStream({ projectId: 3, question: 'q', allowRemote: true, onEvent: () => {} }).finished instanceof Promise, true)
})

// ---- gaps spotted before the mutation run

test('a handler that throws does not break the stream: later events still arrive and the stream still ends', async () => {
  const h = harness()
  let first = true
  const stream = h.ask({ onEvent: (e: unknown) => { h.events.push(e); if (first) { first = false; throw new Error('the page failed to draw') } } })
  h.accept()
  h.emit({ id: stream.id, events: [{ type: 'delta', text: 'a' }, { type: 'delta', text: 'b' }, done] })
  assert.deepEqual(h.events.map((e) => e.type), ['delta', 'delta', 'done'])
  assert.deepEqual(await stream.finished, { ok: true })
  assert.equal(h.unsubscribed(), 1)
})

test('a reply to the start that comes after the stream already ended changes nothing', async () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: stream.id, events: [done] })
  h.refuse(500, { code: 'late', message: 'too late' })
  assert.deepEqual(await stream.finished, { ok: true })
  assert.equal(h.unsubscribed(), 1)
})

test('events keep their order across messages and within a message', () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: stream.id, events: [{ type: 'delta', text: '1' }, { type: 'delta', text: '2' }] })
  h.emit({ id: stream.id, events: [{ type: 'delta', text: '3' }] })
  assert.deepEqual(h.events.map((e) => e.text), ['1', '2', '3'])
})

test('a bad event stops the backend even when the person never pressed Stop, and only once', async () => {
  const h = harness()
  const stream = h.ask()
  h.accept()
  h.emit({ id: stream.id, events: [{ type: 'mystery' }] })
  await stream.stop()
  assert.deepEqual(h.stops, [stream.id])
})

test('the question is sent as typed, spaces and all (the backend trims it)', () => {
  const h = harness()
  h.ask({ question: '  spaced  question  ' })
  assert.equal((h.starts[0].body as { question: string }).question, '  spaced  question  ')
})

test('consent false is sent as false, not left out', () => {
  const h = harness()
  h.ask({ allowRemote: false })
  assert.equal((h.starts[0].body as { allow_remote: boolean }).allow_remote, false)
})


// ---- the checks of the fields, one by one (found by the mutation checks)

for (const key of ['search_ms', 'wait_ms', 'thinking_ms', 'writing_ms', 'total_ms']) {
  test(`a done whose timings lack ${key} is refused`, async () => {
    const h = harness()
    const stream = h.ask()
    const timings: Record<string, unknown> = { ...done.timings }
    delete timings[key]
    h.emit({ id: stream.id, events: [{ ...done, timings }] })
    assert.deepEqual(h.events.map((e) => e.type), ['error'])
    assert.equal(h.events[0].error.code, 'bad_response')
  })

  test(`a done whose ${key} is negative or text is refused`, () => {
    for (const wrong of [-1, 'x', 1.5]) {
      const h = harness()
      const stream = h.ask()
      h.emit({ id: stream.id, events: [{ ...done, timings: { ...done.timings, [key]: wrong } }] })
      assert.equal(h.events[0].type, 'error', `${key} = ${String(wrong)}`)
    }
  })
}

test('a done for a model that does not think (no thinking time, no writing time, no thinking tokens) is accepted', async () => {
  const h = harness()
  const stream = h.ask()
  const quiet = { ...done, timings: { search_ms: 400, wait_ms: 500, thinking_ms: null, writing_ms: null, total_ms: 900 }, thinking: { seen: false, pieces: 0 }, tokens: { thinking: null, answer: null, estimated: true } }
  h.emit({ id: stream.id, events: [quiet] })
  assert.deepEqual(h.events.map((e) => e.type), ['done'])
  assert.deepEqual(await stream.finished, { ok: true })
})

test('a done for an answer where the model was never called (nothing matched) is accepted', () => {
  const h = harness()
  const stream = h.ask()
  const none = { ...done, llm_called: false, model: null, usage: null, sources: [], timings: { search_ms: 300, wait_ms: null, thinking_ms: null, writing_ms: null, total_ms: 310 },
    thinking: { seen: false, pieces: 0 }, tokens: { thinking: null, answer: null, estimated: false }, sent_off_machine: false }
  h.emit({ id: stream.id, events: [none] })
  assert.deepEqual(h.events.map((e) => e.type), ['done'])
})

test('a start for an answer where the model will not be called is accepted', () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: stream.id, events: [{ ...start, llm_called: false, sent_off_machine: false, sources: [] }] })
  assert.deepEqual(h.events.map((e) => e.type), ['start'])
})

test('a message with the right id but events that are not a list is ignored, and the stream goes on listening', () => {
  const h = harness()
  const stream = h.ask()
  for (const events of ['no', 5, null, { 0: { type: 'delta', text: 'x' }, length: 1 }]) h.emit({ id: stream.id, events })
  assert.equal(h.events.length, 0)
  assert.equal(h.listening(), true)
})

test('the error for an event that cannot be used is the one sentence the window shows for an unreadable reply', () => {
  const h = harness()
  const stream = h.ask()
  h.emit({ id: stream.id, events: [{ type: 'mystery' }] })
  assert.deepEqual(h.events[0], { type: 'error', error: { code: 'bad_response', message: 'Clank got a reply it could not read. The app and its backend may be out of date.', status: 0 } })
})

test('a bridge that throws while starting is the fixed sentence', async () => {
  const h = harness({ startStream: async () => { throw new Error('secret detail at port 4321') } })
  const end = await h.ask().finished
  assert.deepEqual(end, { ok: false, error: { code: 'backend_unavailable', message: 'The Clank backend is not reachable.', status: 0 } })
})
