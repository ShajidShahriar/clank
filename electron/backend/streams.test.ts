// Streaming answers, step 1.3, part 2: the door for answers that are written live (StreamManager), tested with a fake network and a fake clock.
// What is promised:
// - the window names an id (so it can stop an answer even while the backend is still searching), a path of exactly one shape and a JSON body; the address, port and
//   token are never its to choose and never reach it; refusals (the backend's own, or a stream that cannot start) come back as an ordinary {ok, status, body} result;
// - events reach ONLY the window that asked; only that window can stop its stream; a stream ends with exactly ONE last event (done, error or cancelled) and nothing after it;
// - what the backend sends is untrusted: lines and the whole stream are size-limited, every line must be a valid event, a silent backend is given up on, a stream that
//   just stops is an error event, and a bad line ends the stream (its good events first);
// - at most about 25 messages a second: the first event of a quiet period goes out at once, the rest are batched (text joined, thinking counts reduced to the latest);
// - closing the window ends its streams quietly; a backend that stops, crashes or restarts ends every stream with an error event; the number of streams is limited.
import test from 'node:test'
import assert from 'node:assert/strict'
import { FLUSH_MS, HEADERS_TIMEOUT_MS, IDLE_TIMEOUT_MS, MAX_PER_OWNER, MAX_TOTAL, StreamManager, type Owner } from './streams.ts'

const TOKEN = 't'.repeat(43)
const PORT = 4321
const PATH = '/projects/3/answer/stream'
const enc = (text: string) => new TextEncoder().encode(text)
const tick = () => new Promise((resolve) => setImmediate(resolve))

class Clock {
  t = 1000
  private timers: Array<{ at: number, fn: () => void, id: number }> = []
  private next = 1
  now = () => this.t
  set = (fn: () => void, ms: number) => { const id = this.next++; this.timers.push({ at: this.t + ms, fn, id }); return id as never }
  clear = (id: never) => { this.timers = this.timers.filter((timer) => timer.id !== (id as number)) }
  pending() { return this.timers.length }
  async advance(ms: number) {
    const target = this.t + ms
    for (;;) {
      this.timers.sort((a, b) => a.at - b.at)
      const due = this.timers[0]
      if (!due || due.at > target) break
      this.timers.shift()
      this.t = due.at
      due.fn()
      await tick()
    }
    this.t = target
    await tick()
  }
}

type Sent = { id: string, events: Array<{ type: string, [k: string]: unknown }> }

function owner(id = 1, throwing = false) {
  const sent: Sent[] = []
  const o: Owner & { sent: Sent[], events: (streamId?: string) => Array<{ type: string, [k: string]: unknown }> } = {
    id,
    send: (message: Sent) => { if (throwing) throw new Error('Object has been destroyed'); sent.push(message) },
    sent,
    events: (streamId?: string) => sent.filter((m) => streamId === undefined || m.id === streamId).flatMap((m) => m.events),
  }
  return o
}

/** A reply the test controls: push text, end, fail. It errors like fetch does when the request's signal is aborted. */
function reply(status = 200, contentType = 'application/x-ndjson') {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const state = { cancelled: false, aborted: false, closed: false }
  const body = new ReadableStream<Uint8Array>({ start(c) { controller = c }, cancel() { state.cancelled = true } })
  return {
    state,
    response: { status, headers: { get: (name: string) => (name.toLowerCase() === 'content-type' ? contentType : null) }, body },
    push: (text: string | Uint8Array) => controller.enqueue(typeof text === 'string' ? enc(text) : text),
    end: () => { state.closed = true; controller.close() },
    fail: (error: Error) => controller.error(error),
  }
}

function setup(extra: Record<string, unknown> = {}) {
  const clock = new Clock()
  const requests: Array<{ url: string, init: Record<string, unknown> }> = []
  const replies: Array<ReturnType<typeof reply>> = []
  let behaviour: (url: string, init: Record<string, unknown>) => Promise<unknown> = async () => { const r = reply(); replies.push(r); return r.response }
  const manager = new StreamManager({
    target: () => ({ port: PORT, token: TOKEN }),
    fetchFn: (async (url: string, init: { signal?: AbortSignal } & Record<string, unknown>) => {
      requests.push({ url, init })
      const result = await behaviour(url, init)
      init.signal?.addEventListener('abort', () => { const last = replies[replies.length - 1]; if (last) last.state.aborted = true })
      return result
    }) as never,
    now: clock.now, setTimer: clock.set, clearTimer: clock.clear, makeId: undefined, ...extra,
  } as never)
  return { manager, clock, requests, replies, setBehaviour: (fn: typeof behaviour) => { behaviour = fn } }
}

async function open(ctx: ReturnType<typeof setup>, o = owner(), id = 'stream-0001', body: unknown = { question: 'q', allow_remote: true }) {
  const result = await ctx.manager.start(o, { id, path: PATH, body })
  assert.equal(result.ok, true, JSON.stringify(result))
  return { o, id, r: ctx.replies[ctx.replies.length - 1] }
}

const line = (event: object) => JSON.stringify(event) + '\n'
const delta = (text: string) => line({ type: 'delta', text })
const done = (answer = 'ok') => line({ type: 'done', answer })

// ---- starting

test('the request is a POST to the backend with the token, the body, the ndjson accept and no redirects', async () => {
  const ctx = setup()
  await open(ctx)
  const { url, init } = ctx.requests[0]
  assert.equal(url, `http://127.0.0.1:${PORT}${PATH}`)
  assert.equal(init.method, 'POST')
  assert.equal((init.headers as Record<string, string>)['x-clank-token'], TOKEN)
  assert.equal((init.headers as Record<string, string>)['accept'], 'application/x-ndjson')
  assert.equal((init.headers as Record<string, string>)['content-type'], 'application/json')
  assert.equal(init.body, JSON.stringify({ question: 'q', allow_remote: true }))
  assert.equal(init.redirect, 'error')
  assert.ok(init.signal instanceof AbortSignal)
})

test('a successful start says ok and the id', async () => {
  const ctx = setup()
  const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} })
  assert.deepEqual(result, { ok: true, status: 200, body: { id: 'abcd1234' } })
  assert.equal(ctx.manager.active, 1)
})

for (const path of ['/health', '/projects/3/answer', '/projects/3/answer/stream/', '/projects/x/answer/stream', '/projects/0/answer/stream', '/projects/03/answer/stream',
  '/projects/3/answer/stream?x=1', '/projects/3/../answer/stream', '//projects/3/answer/stream', '/projects/12345678901/answer/stream', '/projects/3/context', '', 'http://evil.com/projects/3/answer/stream']) {
  test(`the path ${JSON.stringify(path)} is refused and nothing is fetched`, async () => {
    const ctx = setup()
    const result = await ctx.manager.start(owner(), { id: 'abcd1234', path, body: {} })
    assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'bad_request', message: 'The request was not allowed.' } } })
    assert.equal(ctx.requests.length, 0)
  })
}

for (const id of ['', 'short', 'has space!', 'x'.repeat(65), 5, null, undefined, '../etc/passwd', 'a/b/c/d/e/f']) {
  test(`the id ${JSON.stringify(id)} is refused`, async () => {
    const ctx = setup()
    const result = await ctx.manager.start(owner(), { id: id as never, path: PATH, body: {} })
    assert.equal((result as { body: { error: { code: string } } }).body.error.code, 'bad_request')
    assert.equal(ctx.requests.length, 0)
  })
}

for (const body of [[], 'text', 5, true, new Date(), () => 1]) {
  test(`a body that is not a plain object (${String(typeof body)}) is refused`, async () => {
    const ctx = setup()
    const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body })
    assert.equal((result as { body: { error: { code: string } } }).body.error.code, 'bad_request')
    assert.equal(ctx.requests.length, 0)
  })
}

test('a body that is too big is refused', async () => {
  const ctx = setup()
  const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: { question: 'x'.repeat(70_000) } })
  assert.equal((result as { body: { error: { code: string } } }).body.error.code, 'bad_request')
})

test('no backend running is a plain result and nothing is fetched', async () => {
  const ctx = setup({ target: () => null })
  const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} })
  assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'backend_unavailable', message: 'The Clank backend is not running.' } } })
  assert.equal(ctx.requests.length, 0)
})

test('the same id twice for one window is refused; another window may use the same id', async () => {
  const ctx = setup()
  const a = owner(1)
  await open(ctx, a, 'same-id-01')
  const again = await ctx.manager.start(a, { id: 'same-id-01', path: PATH, body: {} })
  assert.equal((again as { body: { error: { code: string } } }).body.error.code, 'bad_request')
  const other = await ctx.manager.start(owner(2), { id: 'same-id-01', path: PATH, body: {} })
  assert.equal(other.ok, true)
})

// ---- what the backend answers before streaming

test('the backend\'s own refusal comes back as an ordinary result, with its status and body, and nothing streams', async () => {
  const ctx = setup()
  const refusal = { error: { code: 'consent_required', message: 'The answer model is a remote service.' } }
  ctx.setBehaviour(async () => ({ status: 403, headers: { get: () => 'application/json' }, body: new Response(JSON.stringify(refusal)).body }))
  const o = owner()
  const result = await ctx.manager.start(o, { id: 'abcd1234', path: PATH, body: {} })
  assert.deepEqual(result, { ok: false, status: 403, body: refusal })
  assert.equal(ctx.manager.active, 0)
  assert.equal(o.sent.length, 0)
})

test('a refusal that is not JSON, or is huge, is a bad_response and never passed on', async () => {
  for (const text of ['<html>oops</html>', 'x'.repeat(200_000), '']) {
    const ctx = setup()
    ctx.setBehaviour(async () => ({ status: 500, headers: { get: () => 'text/html' }, body: new Response(text).body }))
    const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} }) as { ok: boolean, status: number, body: { error: { code: string } } }
    assert.equal(result.ok, false)
    assert.equal(result.status, 500)
    assert.equal(result.body.error.code, 'bad_response')
    assert.equal(JSON.stringify(result).includes('oops'), false)
  }
})

test('a 200 that is not a stream is a bad_response', async () => {
  const ctx = setup()
  const r = reply(200, 'text/html')
  ctx.replies.push(r)
  ctx.setBehaviour(async () => r.response)
  const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} }) as { ok: boolean, body: { error: { code: string } } }
  assert.equal(result.ok, false)
  assert.equal(result.body.error.code, 'bad_response')
  assert.equal(ctx.manager.active, 0)
  assert.equal(r.state.cancelled, true, 'its connection is closed')
})

test('a backend that cannot be reached is a fixed message, with no token and no port in it', async () => {
  const ctx = setup()
  ctx.setBehaviour(async () => { throw new Error(`connect ECONNREFUSED 127.0.0.1:${PORT} token ${TOKEN}`) })
  const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} })
  assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'backend_unavailable', message: 'The Clank backend is not reachable.' } } })
  assert.equal(ctx.manager.active, 0)
})

test('a backend that never answers the request is given up on after the headers timeout', async () => {
  const ctx = setup()
  ctx.setBehaviour((_url, init) => new Promise((_resolve, reject) => { (init.signal as AbortSignal).addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))) }))
  let result: unknown
  const pending = ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} }).then((r) => { result = r })
  await tick()
  await ctx.clock.advance(HEADERS_TIMEOUT_MS - 1)
  assert.equal(result, undefined)
  await ctx.clock.advance(2)
  await pending
  assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'backend_timeout', message: 'The Clank backend took too long to answer.' } } })
  assert.equal(ctx.manager.active, 0)
})

// ---- delivery and batching

test('the first event is delivered at once, the next ones are batched at the end of the interval', async () => {
  const ctx = setup()
  const { o, id, r } = await open(ctx)
  r.push(line({ type: 'start', sources: [] }))
  await tick()
  assert.deepEqual(o.sent, [{ id, events: [{ type: 'start', sources: [] }] }])
  r.push(delta('a') + delta('b') + delta('c'))
  await tick()
  assert.equal(o.sent.length, 1, 'held back until the interval is over')
  await ctx.clock.advance(FLUSH_MS)
  assert.deepEqual(o.sent[1], { id, events: [{ type: 'delta', text: 'abc' }] })
})

test('no more than about 25 messages a second, and not a character lost', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  let expected = ''
  for (let i = 0; i < 200; i++) {                      // 200 pieces, one every 5 ms: one second
    const piece = `w${i} `
    expected += piece
    r.push(delta(piece))
    await tick()
    await ctx.clock.advance(5)
  }
  await ctx.clock.advance(FLUSH_MS)
  assert.ok(o.sent.length <= 27, `${o.sent.length} messages`)
  assert.equal(o.events().map((e) => e.text).join(''), expected)
})

test('a slow stream is not delayed at all: each piece after a quiet moment goes out at once', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  for (const text of ['a', 'b', 'c']) {
    r.push(delta(text))
    await tick()
    await ctx.clock.advance(500)
  }
  assert.deepEqual(o.events().map((e) => e.text), ['a', 'b', 'c'])
  assert.equal(o.sent.length, 3)
})

test('thinking counts are reduced to the latest, text is joined, and the order is kept', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(line({ type: 'start' }))
  await tick()
  r.push(line({ type: 'stage', stage: 'thinking', at_ms: 1 }) + line({ type: 'thinking', pieces: 1 }) + line({ type: 'thinking', pieces: 2 }) + line({ type: 'thinking', pieces: 3 })
    + line({ type: 'stage', stage: 'writing', at_ms: 2 }) + delta('x') + delta('y'))
  await tick()
  await ctx.clock.advance(FLUSH_MS)
  assert.deepEqual(o.sent[1].events.map((e) => e.type), ['stage', 'thinking', 'stage', 'delta'])
  assert.equal(o.sent[1].events[1].pieces, 3)
  assert.equal(o.sent[1].events[3].text, 'xy')
})

test('events cut across chunks and several lines in one chunk are all read', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  const text = delta('é🙂') + delta('two') + done()
  const bytes = enc(text)
  for (let i = 0; i < bytes.length; i += 7) { r.push(bytes.slice(i, i + 7)); await tick() }
  await ctx.clock.advance(FLUSH_MS)
  const types = o.events().map((e) => e.type)
  assert.equal(types.at(-1), 'done')
  assert.equal(types.slice(0, -1).every((t) => t === 'delta'), true)
  assert.equal(o.events().filter((e) => e.type === 'delta').map((e) => e.text).join(''), 'é🙂two')
})

// ---- the end of a stream: exactly one last event

test('done is the last event: delivered with what was waiting, the stream is closed and removed', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(delta('a'))
  await tick()
  r.push(delta('b') + done('ab'))
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['delta', 'delta', 'done'])
  assert.equal(o.events().at(-1)?.answer, 'ab')
  assert.equal(ctx.manager.active, 0)
  assert.equal(r.state.cancelled, true)
})

test('an error event ends the stream the same way', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(delta('half') + line({ type: 'error', error: { code: 'llm_timeout', message: 'slow', status: 504 } }))
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['delta', 'error'])
  assert.equal(ctx.manager.active, 0)
})

test('nothing after the last event is delivered', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(done() + delta('late') + delta('later'))
  await tick()
  await ctx.clock.advance(1000)
  assert.deepEqual(o.events().map((e) => e.type), ['done'])
})

test('a stream that just stops is an error event, after the good events', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(delta('some'))
  await tick()
  r.end()
  await tick()
  await ctx.clock.advance(FLUSH_MS)
  assert.deepEqual(o.events().map((e) => e.type), ['delta', 'error'])
  const error = o.events().at(-1) as unknown as { error: { code: string, message: string, status: number } }
  assert.deepEqual(error.error, { code: 'backend_unavailable', message: 'The Clank backend stopped answering before the answer was complete.', status: 0 })
  assert.equal(ctx.manager.active, 0)
})

test('a connection that breaks in the middle is the same error event', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(delta('some'))
  await tick()
  r.fail(new Error(`read ECONNRESET 127.0.0.1:${PORT}`))
  await tick()
  await ctx.clock.advance(FLUSH_MS)
  assert.equal(o.events().at(-1)?.type, 'error')
  assert.equal(JSON.stringify(o.sent).includes('ECONNRESET'), false)
  assert.equal(JSON.stringify(o.sent).includes(String(PORT)), false)
})

for (const [name, bad] of [['not JSON', 'oops\n'], ['not an object', '[1]\n'], ['an unknown type', '{"type":"mystery"}\n'], ['the app\'s own cancelled', '{"type":"cancelled"}\n']] as const) {
  test(`a line that is ${name} ends the stream with a bad_response error, after the good events`, async () => {
    const ctx = setup()
    const { o, r } = await open(ctx)
    r.push(delta('good') + bad + delta('never'))
    await tick()
    await ctx.clock.advance(FLUSH_MS)
    assert.deepEqual(o.events().map((e) => e.type), ['delta', 'error'])
    assert.equal((o.events().at(-1) as unknown as { error: { code: string } }).error.code, 'bad_response')
    assert.equal(o.events()[0].text, 'good')
    assert.equal(ctx.manager.active, 0)
    assert.equal(r.state.cancelled, true)
  })
}

test('a line that is far too long ends the stream with a bad_response error', async () => {
  const ctx = setup({ limits: { maxLineBytes: 100 } })
  const { o, r } = await open(ctx)
  r.push('x'.repeat(150))
  await tick()
  assert.equal((o.events().at(-1) as unknown as { error: { code: string } }).error.code, 'bad_response')
  assert.equal(ctx.manager.active, 0)
})

test('a stream that is far too big ends with a bad_response error', async () => {
  const ctx = setup({ limits: { maxTotalBytes: 300 } })
  const { o, r } = await open(ctx)
  for (let i = 0; i < 20 && ctx.manager.active > 0; i++) { r.push(delta('x'.repeat(30))); await tick() }
  assert.equal((o.events().at(-1) as unknown as { error: { code: string } }).error.code, 'bad_response')
  assert.equal(ctx.manager.active, 0)
})

// ---- a backend that goes silent

test('a silent backend is given up on after the idle time, with a timeout error event', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(delta('a'))
  await tick()
  await ctx.clock.advance(IDLE_TIMEOUT_MS + 1)
  assert.equal((o.events().at(-1) as unknown as { error: { code: string } }).error.code, 'backend_timeout')
  assert.equal(ctx.manager.active, 0)
  assert.equal(r.state.cancelled, true)
})

test('every chunk of data restarts the idle time', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  for (let i = 0; i < 4; i++) {
    await ctx.clock.advance(IDLE_TIMEOUT_MS - 1000)
    r.push(delta('.'))
    await tick()
  }
  assert.equal(ctx.manager.active, 1)
  assert.equal(o.events().every((e) => e.type === 'delta'), true)
})

test('the idle time is longer than any model\'s own timeout (180 seconds)', () => {
  assert.ok(IDLE_TIMEOUT_MS > 180_000)
  assert.ok(HEADERS_TIMEOUT_MS > 180_000)
})

// ---- stopping

test('stop aborts the request and the window gets exactly one cancelled event, without the batch that was waiting', async () => {
  const ctx = setup()
  const { o, id, r } = await open(ctx)
  r.push(delta('a'))
  await tick()
  r.push(delta('b'))
  await tick()
  assert.equal(ctx.manager.stop(o.id, id), true)
  await tick()
  await ctx.clock.advance(1000)
  assert.deepEqual(o.events().map((e) => e.type), ['delta', 'cancelled'])
  assert.equal(r.state.cancelled || r.state.aborted, true)
  assert.equal((ctx.requests[0].init.signal as AbortSignal).aborted, true)
  assert.equal(ctx.manager.active, 0)
})

test('stopping twice, or an unknown stream, does nothing', async () => {
  const ctx = setup()
  const { o, id } = await open(ctx)
  assert.equal(ctx.manager.stop(o.id, id), true)
  assert.equal(ctx.manager.stop(o.id, id), false)
  assert.equal(ctx.manager.stop(o.id, 'no-such-stream'), false)
  assert.equal(o.events().filter((e) => e.type === 'cancelled').length, 1)
})

test('another window cannot stop a stream that is not its own', async () => {
  const ctx = setup()
  const { o, id, r } = await open(ctx, owner(1))
  assert.equal(ctx.manager.stop(2, id), false)
  r.push(delta('still'))
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['delta'])
  assert.equal(ctx.manager.active, 1)
})

test('stopping while the backend is still working on the request ends the start with cancelled and delivers nothing', async () => {
  const ctx = setup()
  ctx.setBehaviour((_url, init) => new Promise((_resolve, reject) => { (init.signal as AbortSignal).addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))) }))
  const o = owner()
  let result: unknown
  const pending = ctx.manager.start(o, { id: 'abcd1234', path: PATH, body: {} }).then((r) => { result = r })
  await tick()
  assert.equal(ctx.manager.stop(o.id, 'abcd1234'), true)
  await pending
  assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'cancelled', message: 'The answer was stopped.' } } })
  assert.equal(o.sent.length, 0)
  assert.equal(ctx.manager.active, 0)
})

test('a stop that arrives after the stream ended on its own is false and adds no event', async () => {
  const ctx = setup()
  const { o, id, r } = await open(ctx)
  r.push(done())
  await tick()
  assert.equal(ctx.manager.stop(o.id, id), false)
  assert.deepEqual(o.events().map((e) => e.type), ['done'])
})

// ---- the window goes away, the backend goes away

test('closing a window ends its streams quietly and leaves the other windows\' alone', async () => {
  const ctx = setup()
  const a = await open(ctx, owner(1), 'a-stream-01')
  const b = await open(ctx, owner(2), 'b-stream-01')
  ctx.manager.closeOwner(1)
  await tick()
  assert.equal(a.o.sent.length, 0, 'nothing is sent to a window that is gone')
  assert.equal((ctx.requests[0].init.signal as AbortSignal).aborted, true)
  assert.equal((ctx.requests[1].init.signal as AbortSignal).aborted, false)
  b.r.push(delta('ok'))
  await tick()
  assert.deepEqual(b.o.events().map((e) => e.type), ['delta'])
  assert.equal(ctx.manager.active, 1)
})

test('when the backend stops, every stream ends with an error event and is aborted', async () => {
  const ctx = setup()
  const a = await open(ctx, owner(1), 'a-stream-01')
  const b = await open(ctx, owner(2), 'b-stream-01')
  a.r.push(delta('partial'))
  await tick()
  ctx.manager.abortAll()
  await tick()
  for (const w of [a, b]) {
    const last = w.o.events().at(-1) as unknown as { type: string, error: { code: string, message: string } }
    assert.equal(last.type, 'error')
    assert.deepEqual(last.error, { code: 'backend_unavailable', message: 'The Clank backend stopped.', status: 0 })
  }
  assert.equal(ctx.manager.active, 0)
  assert.equal(ctx.requests.every((q) => (q.init.signal as AbortSignal).aborted), true)
})

test('a start that is still waiting when the backend stops fails as backend_unavailable', async () => {
  const ctx = setup()
  ctx.setBehaviour((_url, init) => new Promise((_resolve, reject) => { (init.signal as AbortSignal).addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))) }))
  let result: unknown
  const pending = ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} }).then((r) => { result = r })
  await tick()
  ctx.manager.abortAll()
  await pending
  assert.equal((result as { body: { error: { code: string } } }).body.error.code, 'backend_unavailable')
})

test('a window that cannot be written to (destroyed) ends its stream quietly', async () => {
  const ctx = setup()
  const gone = owner(1, true)
  const { r } = await open(ctx, gone)
  r.push(delta('a'))
  await tick()
  assert.equal(ctx.manager.active, 0)
  assert.equal((ctx.requests[0].init.signal as AbortSignal).aborted, true)
})

// ---- limits and secrets

test('a window can have only so many streams, and the whole app only so many', async () => {
  const ctx = setup()
  const a = owner(1)
  for (let i = 0; i < MAX_PER_OWNER; i++) await open(ctx, a, `stream-a-${i}`)
  const refused = await ctx.manager.start(a, { id: 'stream-a-x', path: PATH, body: {} })
  assert.deepEqual(refused, { ok: false, status: 0, body: { error: { code: 'too_many_streams', message: 'Too many answers are being written at once.' } } })
  for (let w = 2; w < 2 + Math.ceil(MAX_TOTAL / MAX_PER_OWNER); w++) {
    for (let i = 0; i < MAX_PER_OWNER && ctx.manager.active < MAX_TOTAL; i++) await open(ctx, owner(w), `stream-${w}-${i}`)
  }
  assert.equal(ctx.manager.active, MAX_TOTAL)
  const full = await ctx.manager.start(owner(99), { id: 'stream-z-1', path: PATH, body: {} })
  assert.equal((full as { body: { error: { code: string } } }).body.error.code, 'too_many_streams')
})

test('a finished stream frees its place', async () => {
  const ctx = setup()
  const a = owner(1)
  const first = await open(ctx, a, 'stream-a-0')
  for (let i = 1; i < MAX_PER_OWNER; i++) await open(ctx, a, `stream-a-${i}`)
  first.r.push(done())
  await tick()
  assert.equal((await ctx.manager.start(a, { id: 'stream-a-new', path: PATH, body: {} })).ok, true)
})

test('neither the token nor the port is ever in anything the window receives', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(line({ type: 'start', sources: [] }) + delta('hello') + 'bad line\n')
  await tick()
  await ctx.clock.advance(1000)
  const everything = JSON.stringify(o.sent)
  assert.equal(everything.includes(TOKEN), false)
  assert.equal(everything.includes(String(PORT)), false)
  assert.equal(everything.includes('127.0.0.1'), false)
})

test('events go only to the window that asked', async () => {
  const ctx = setup()
  const a = await open(ctx, owner(1), 'a-stream-01')
  const b = await open(ctx, owner(2), 'b-stream-01')
  a.r.push(delta('for-a'))
  b.r.push(delta('for-b'))
  await tick()
  assert.deepEqual(a.o.events().map((e) => e.text), ['for-a'])
  assert.deepEqual(b.o.events().map((e) => e.text), ['for-b'])
  assert.equal(a.o.sent.every((m) => m.id === 'a-stream-01'), true)
})

test('timers do not pile up: a finished stream leaves none behind', async () => {
  const ctx = setup()
  const { r } = await open(ctx)
  r.push(delta('a'))
  await tick()
  r.push(delta('b'))
  await tick()
  r.push(done())
  await tick()
  assert.equal(ctx.clock.pending(), 0)
})

// ---- gaps found by the mutation checks

test('a healthy stream is not aborted by the timer of the wait for the first answer (it is cleared when the answer starts)', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  for (let i = 0; i < 4; i++) {                                  // 400 seconds in all: longer than the wait for the headers, but data keeps coming
    await ctx.clock.advance(100_000)
    r.push(delta(`${i}`))
    await tick()
  }
  assert.equal((ctx.requests[0].init.signal as AbortSignal).aborted, false)
  assert.equal(ctx.manager.active, 1)
  assert.equal(o.events().length >= 4, true)
})

test('a start still waiting when the backend stops says so, in the backend\'s words', async () => {
  const ctx = setup()
  ctx.setBehaviour((_url, init) => new Promise((_resolve, reject) => { (init.signal as AbortSignal).addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))) }))
  let result: unknown
  const pending = ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} }).then((r) => { result = r })
  await tick()
  ctx.manager.abortAll()
  await pending
  assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'backend_unavailable', message: 'The Clank backend stopped.' } } })
})

test('a fetch that ignores the abort and answers anyway still ends in a cancelled start, and its connection is closed', async () => {
  const ctx = setup()
  const r = reply()
  ctx.replies.push(r)
  let release!: () => void
  ctx.setBehaviour(() => new Promise((resolve) => { release = () => resolve(r.response) }))
  const o = owner()
  let result: unknown
  const pending = ctx.manager.start(o, { id: 'abcd1234', path: PATH, body: {} }).then((x) => { result = x })
  await tick()
  assert.equal(ctx.manager.stop(o.id, 'abcd1234'), true)
  release()
  await pending
  assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'cancelled', message: 'The answer was stopped.' } } })
  assert.equal(r.state.cancelled, true)
  assert.equal(o.sent.length, 0)
  assert.equal(ctx.manager.active, 0)
})

test('a refusal that is valid JSON but huge is a bad_response, and is not passed on', async () => {
  const ctx = setup()
  const huge = JSON.stringify({ error: { code: 'x', message: 'y'.repeat(200_000) } })
  ctx.setBehaviour(async () => ({ status: 500, headers: { get: () => 'application/json' }, body: new Response(huge).body }))
  const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} }) as { ok: boolean, status: number, body: { error: { code: string } } }
  assert.equal(result.ok, false)
  assert.equal(result.status, 500)
  assert.equal(result.body.error.code, 'bad_response')
  assert.equal(JSON.stringify(result).length < 1000, true)
})

for (const refusal of ['{"error":"text"}', '{"detail":"x"}', '{"error":{"code":5,"message":"m"}}', '{"error":{"code":"c"}}', '{"error":{"message":"m"}}', '{"error":null}', '[]', 'null', '5']) {
  test(`a refusal shaped like ${refusal} is a bad_response`, async () => {
    const ctx = setup()
    ctx.setBehaviour(async () => ({ status: 422, headers: { get: () => 'application/json' }, body: new Response(refusal).body }))
    const result = await ctx.manager.start(owner(), { id: 'abcd1234', path: PATH, body: {} }) as { ok: boolean, status: number, body: { error: { code: string } } }
    assert.equal(result.status, 422)
    assert.equal(result.body.error.code, 'bad_response')
  })
}

test('a backend that says 200 and then nothing at all is given up on after the idle time', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  await ctx.clock.advance(IDLE_TIMEOUT_MS + 1)
  assert.deepEqual(o.events().map((e) => e.type), ['error'])
  assert.equal((o.events()[0] as unknown as { error: { code: string } }).error.code, 'backend_timeout')
  assert.equal(r.state.cancelled, true)
})

test('a last line with no newline is read when the stream ends', async () => {
  const ctx = setup()
  const { o, r } = await open(ctx)
  r.push(JSON.stringify({ type: 'done', answer: 'ok' }))          // no "\n"
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), [])
  r.end()
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['done'])
})

test('a flush that comes before its timer clears the timer', async () => {
  const ctx = setup()
  const { r } = await open(ctx)
  r.push(delta('a'))
  await tick()
  r.push(delta('b'))                                              // held back: a timer is set
  await tick()
  assert.equal(ctx.clock.pending() >= 1, true)
  ctx.clock.t += 10_000                                           // the time passes without the timer being run
  r.push(delta('c'))                                              // enough time has passed: this flushes at once
  await tick()
  assert.equal(ctx.clock.pending(), 1, 'only the idle timer is left')
})

// ---- the one last event, even when the window answers it at once

test('a window that stops the stream from inside the delivery of "done" cannot add a second last event', async () => {
  const ctx = setup()
  const o = owner()
  let stopResult: boolean | null = null
  const original = o.send
  o.send = (message) => {
    original(message)
    if (message.events.some((e) => e.type === 'done')) stopResult = ctx.manager.stop(o.id, 'stream-0001')
  }
  const { r } = await open(ctx, o)
  r.push(done())
  await tick()
  assert.equal(stopResult, false)
  assert.deepEqual(o.events().map((e) => e.type), ['done'])
})

test('closing or aborting from inside the delivery of "done" adds nothing either', async () => {
  const ctx = setup()
  const o = owner()
  const original = o.send
  o.send = (message) => {
    original(message)
    if (message.events.some((e) => e.type === 'done')) { ctx.manager.closeOwner(o.id); ctx.manager.abortAll() }
  }
  const { r } = await open(ctx, o)
  r.push(done())
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['done'])
  assert.equal(ctx.manager.active, 0)
})

test('an error event delivered to a window that stops at once is also the only last event', async () => {
  const ctx = setup()
  const o = owner()
  const original = o.send
  o.send = (message) => {
    original(message)
    if (message.events.some((e) => e.type === 'error')) ctx.manager.stop(o.id, 'stream-0001')
  }
  const { r } = await open(ctx, o)
  r.push(line({ type: 'error', error: { code: 'x', message: 'y', status: 0 } }))
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['error'])
})

test('a backend that stops from inside the delivery of "done" adds no error after it', async () => {
  const ctx = setup()
  const o = owner()
  const original = o.send
  o.send = (message) => {
    original(message)
    if (message.events.some((e) => e.type === 'done')) ctx.manager.abortAll()          // (only this: the stream is still known to the manager at this moment)
  }
  const { r } = await open(ctx, o)
  r.push(done())
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['done'])
  assert.equal(ctx.manager.active, 0)
})

test('a backend that stops from inside the delivery of an error adds nothing either', async () => {
  const ctx = setup()
  const o = owner()
  const original = o.send
  o.send = (message) => {
    original(message)
    if (message.events.some((e) => e.type === 'error')) ctx.manager.abortAll()
  }
  const { r } = await open(ctx, o)
  r.push(line({ type: 'error', error: { code: 'x', message: 'y', status: 0 } }))
  await tick()
  assert.deepEqual(o.events().map((e) => e.type), ['error'])
})
