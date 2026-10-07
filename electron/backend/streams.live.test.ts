// Streaming answers, step 1.3, part 3: the door against a REAL HTTP server with Node's real fetch (what the main process uses): lines arrive as they are written, Stop makes
// the server see the connection close (which is how the backend, and through it the model, is told to stop), and the failure cases end the way they are promised to.
import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { FLUSH_MS, StreamManager, type Owner } from './streams.ts'

const TOKEN = 'k'.repeat(43)
const PATH = '/projects/3/answer/stream'
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const line = (event: object) => JSON.stringify(event) + '\n'

type Seen = { method?: string, url?: string, token?: string, accept?: string, type?: string, body?: string }

async function serve(handler: (req: http.IncomingMessage, res: http.ServerResponse, seen: Seen) => void) {
  const seen: Seen = {}
  const sockets = new Set<import('node:net').Socket>()
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (chunk) => { body += chunk })
    req.on('end', () => {
      Object.assign(seen, { method: req.method, url: req.url, token: req.headers['x-clank-token'], accept: req.headers.accept, type: req.headers['content-type'], body })
      handler(req, res, seen)
    })
  })
  server.on('connection', (socket) => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)) })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const port = (server.address() as AddressInfo).port
  return { port, seen, close: async () => { for (const s of sockets) s.destroy(); await new Promise((resolve) => server.close(resolve)) } }
}

/** Resolves when the client closes its end of the response (a Stop, an abort or a vanished window). */
function closedPromise(res: http.ServerResponse): Promise<number> {
  return new Promise((resolve) => res.on('close', () => resolve(Date.now())))
}

function owner(id = 1) {
  const sent: Array<{ at: number, id: string, events: Array<{ type: string, [k: string]: unknown }> }> = []
  const o: Owner & { sent: typeof sent, events: () => Array<{ type: string, [k: string]: unknown }> } = {
    id, send: (message) => { sent.push({ at: Date.now(), ...message } as never) }, sent, events: () => sent.flatMap((m) => m.events),
  }
  return o
}

const ndjson = { 'content-type': 'application/x-ndjson', 'cache-control': 'no-store' }

async function waitFor(condition: () => boolean, ms = 3000) {
  const end = Date.now() + ms
  while (Date.now() < end && !condition()) await sleep(10)
  return condition()
}

test('pieces arrive over time, the request carries the token and the body, and the stream ends with done', async () => {
  const backend = await serve(async (_req, res) => {
    res.writeHead(200, ndjson)
    res.write(line({ type: 'start', sources: [] }))
    for (const word of ['one ', 'two ', 'three ', 'four ']) { await sleep(120); res.write(line({ type: 'delta', text: word })) }
    await sleep(120)
    res.end(line({ type: 'done', answer: 'one two three four ' }))
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    const started = Date.now()
    const result = await manager.start(o, { id: 'live-0001', path: PATH, body: { question: 'q', allow_remote: true } })
    assert.deepEqual(result, { ok: true, status: 200, body: { id: 'live-0001' } })
    assert.equal(await waitFor(() => o.events().at(-1)?.type === 'done'), true)
    assert.deepEqual(backend.seen, { method: 'POST', url: PATH, token: TOKEN, accept: 'application/x-ndjson', type: 'application/json', body: '{"question":"q","allow_remote":true}' })
    const texts = o.sent.filter((m) => m.events.some((e) => e.type === 'delta'))
    assert.ok(texts.length >= 3, 'the text came in several messages')
    assert.ok(texts.at(-1)!.at - texts[0].at >= 300, 'spread over time, not all at the end')
    assert.ok(texts[0].at - started < 500, 'the first piece was not held back')
    assert.equal(o.events().filter((e) => e.type === 'delta').map((e) => e.text).join(''), 'one two three four ')
    assert.equal(manager.active, 0)
    assert.equal(JSON.stringify(o.sent).includes(TOKEN) || JSON.stringify(o.sent).includes(String(backend.port)), false)
  } finally { await backend.close() }
})

test('Stop makes the server see the connection close, and the window gets one cancelled event', async () => {
  let closed: Promise<number> = Promise.resolve(0)
  const backend = await serve((_req, res) => {
    res.writeHead(200, ndjson)
    res.write(line({ type: 'start' }) + line({ type: 'delta', text: 'a' }))
    closed = closedPromise(res)                                           // then silence: the backend is waiting on a slow model
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    await manager.start(o, { id: 'live-0002', path: PATH, body: {} })
    assert.equal(await waitFor(() => o.events().some((e) => e.type === 'delta')), true)
    const stoppedAt = Date.now()
    assert.equal(manager.stop(o.id, 'live-0002'), true)
    const closedAt = await Promise.race([closed, sleep(3000).then(() => -1)])
    assert.ok(closedAt > 0, 'the server never saw the connection close')
    assert.ok(closedAt - stoppedAt < 1000, `${closedAt - stoppedAt} ms`)
    assert.deepEqual(o.events().map((e) => e.type), ['start', 'delta', 'cancelled'])
    assert.equal(o.events().filter((e) => e.type === 'cancelled').length, 1)
  } finally { await backend.close() }
})

test('the backend\'s refusal is an ordinary result', async () => {
  const refusal = { error: { code: 'consent_required', message: 'The answer model is a remote service.' } }
  const backend = await serve((_req, res) => { res.writeHead(403, { 'content-type': 'application/json' }); res.end(JSON.stringify(refusal)) })
  try {
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    const o = owner()
    assert.deepEqual(await manager.start(o, { id: 'live-0003', path: PATH, body: {} }), { ok: false, status: 403, body: refusal })
    assert.equal(manager.active, 0)
    assert.equal(o.sent.length, 0)
  } finally { await backend.close() }
})

test('a backend that is not there is the fixed message', async () => {
  const backend = await serve(() => {})
  const port = backend.port
  await backend.close()
  const manager = new StreamManager({ target: () => ({ port, token: TOKEN }) })
  const result = await manager.start(owner(), { id: 'live-0004', path: PATH, body: {} })
  assert.deepEqual(result, { ok: false, status: 0, body: { error: { code: 'backend_unavailable', message: 'The Clank backend is not reachable.' } } })
})

test('a finished stream closes its connection', async () => {
  let closed: Promise<number> = Promise.resolve(0)
  const backend = await serve((_req, res) => {
    res.writeHead(200, ndjson)
    closed = closedPromise(res)
    res.write(line({ type: 'done', answer: 'x' }))                         // and does NOT end: the app must close the connection itself
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    await manager.start(o, { id: 'live-0005', path: PATH, body: {} })
    assert.equal(await waitFor(() => o.events().some((e) => e.type === 'done')), true)
    assert.ok((await Promise.race([closed, sleep(3000).then(() => -1)])) > 0)
  } finally { await backend.close() }
})

test('a line that is not an event ends the stream with a bad_response error and closes the connection', async () => {
  let closed: Promise<number> = Promise.resolve(0)
  const backend = await serve((_req, res) => {
    res.writeHead(200, ndjson)
    closed = closedPromise(res)
    res.write(line({ type: 'delta', text: 'good' }) + 'this is not json\n' + line({ type: 'delta', text: 'never' }))
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    await manager.start(o, { id: 'live-0006', path: PATH, body: {} })
    assert.equal(await waitFor(() => o.events().some((e) => e.type === 'error')), true)
    assert.deepEqual(o.events().map((e) => e.type), ['delta', 'error'])
    assert.equal((o.events().at(-1) as unknown as { error: { code: string } }).error.code, 'bad_response')
    assert.ok((await Promise.race([closed, sleep(3000).then(() => -1)])) > 0)
  } finally { await backend.close() }
})

test('a backend that hangs up in the middle is an error event', async () => {
  const backend = await serve((_req, res) => {
    res.writeHead(200, ndjson)
    res.write(line({ type: 'delta', text: 'half' }))
    setTimeout(() => res.socket?.destroy(), 100)
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    await manager.start(o, { id: 'live-0007', path: PATH, body: {} })
    assert.equal(await waitFor(() => o.events().some((e) => e.type === 'error')), true)
    assert.deepEqual(o.events().map((e) => e.type), ['delta', 'error'])
    assert.equal(JSON.stringify(o.sent).includes('ECONNRESET'), false)
  } finally { await backend.close() }
})

test('the backend stopping ends every stream with an error event and closes the connections', async () => {
  const closes: Array<Promise<number>> = []
  const backend = await serve((_req, res) => {
    res.writeHead(200, ndjson)
    res.write(line({ type: 'start' }))
    closes.push(closedPromise(res))
  })
  try {
    const a = owner(1)
    const b = owner(2)
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    await manager.start(a, { id: 'live-000a', path: PATH, body: {} })
    await manager.start(b, { id: 'live-000b', path: PATH, body: {} })
    assert.equal(await waitFor(() => a.events().length > 0 && b.events().length > 0), true)
    manager.abortAll()
    for (const w of [a, b]) assert.equal(w.events().at(-1)?.type, 'error')
    const results = await Promise.all(closes.map((c) => Promise.race([c, sleep(3000).then(() => -1)])))
    assert.equal(results.every((t) => t > 0), true)
    assert.equal(manager.active, 0)
  } finally { await backend.close() }
})

test('closing a window closes its connection and tells it nothing', async () => {
  let closed: Promise<number> = Promise.resolve(0)
  const backend = await serve((_req, res) => {
    res.writeHead(200, ndjson)
    res.write(line({ type: 'start' }))
    closed = closedPromise(res)
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    await manager.start(o, { id: 'live-0008', path: PATH, body: {} })
    assert.equal(await waitFor(() => o.events().length > 0), true)
    const before = o.sent.length
    manager.closeOwner(o.id)
    assert.ok((await Promise.race([closed, sleep(3000).then(() => -1)])) > 0)
    await sleep(100)
    assert.equal(o.sent.length, before)
  } finally { await backend.close() }
})

test('a long fast answer arrives complete, in at most about 25 messages a second', async () => {
  const words = Array.from({ length: 3000 }, (_, i) => `w${i} `)
  const backend = await serve(async (_req, res) => {
    res.writeHead(200, ndjson)
    res.write(line({ type: 'start' }))
    for (let i = 0; i < words.length; i += 10) {
      res.write(words.slice(i, i + 10).map((w) => line({ type: 'delta', text: w })).join(''))
      await sleep(2)
    }
    res.end(line({ type: 'done', answer: words.join('') }))
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    const started = Date.now()
    await manager.start(o, { id: 'live-0009', path: PATH, body: {} })
    assert.equal(await waitFor(() => o.events().at(-1)?.type === 'done', 10000), true)
    const seconds = (Date.now() - started) / 1000
    assert.equal(o.events().filter((e) => e.type === 'delta').map((e) => e.text).join(''), words.join(''))
    assert.ok(o.sent.length <= Math.ceil(seconds * (1000 / FLUSH_MS)) + 3, `${o.sent.length} messages in ${seconds.toFixed(2)} s`)
  } finally { await backend.close() }
})

test('a character cut across TCP packets arrives whole', async () => {
  const bytes = Buffer.from(line({ type: 'delta', text: 'a🙂é日' }))
  const backend = await serve(async (_req, res) => {
    res.writeHead(200, ndjson)
    for (let i = 0; i < bytes.length; i += 3) { res.write(bytes.subarray(i, i + 3)); await sleep(5) }
    res.end(line({ type: 'done', answer: 'a🙂é日' }))
  })
  try {
    const o = owner()
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    await manager.start(o, { id: 'live-0010', path: PATH, body: {} })
    assert.equal(await waitFor(() => o.events().at(-1)?.type === 'done'), true)
    assert.equal(o.events().find((e) => e.type === 'delta')?.text, 'a🙂é日')
  } finally { await backend.close() }
})

test('two windows at once each get only their own stream', async () => {
  const backend = await serve(async (_req, res, seen) => {
    const who = JSON.parse(seen.body ?? '{}').who
    res.writeHead(200, ndjson)
    for (let i = 0; i < 3; i++) { res.write(line({ type: 'delta', text: `${who}${i} ` })); await sleep(60) }
    res.end(line({ type: 'done', answer: who }))
  })
  try {
    const a = owner(1)
    const b = owner(2)
    const manager = new StreamManager({ target: () => ({ port: backend.port, token: TOKEN }) })
    // (the fake handler reads `seen`, which is shared: give each request its own moment)
    await manager.start(a, { id: 'live-00aa', path: PATH, body: { who: 'A' } })
    await sleep(30)
    await manager.start(b, { id: 'live-00bb', path: PATH, body: { who: 'B' } })
    assert.equal(await waitFor(() => a.events().at(-1)?.type === 'done' && b.events().at(-1)?.type === 'done'), true)
    assert.equal(a.sent.every((m) => m.id === 'live-00aa'), true)
    assert.equal(b.sent.every((m) => m.id === 'live-00bb'), true)
    assert.equal(a.events().some((e) => String(e.text ?? '').startsWith('B')), false)
  } finally { await backend.close() }
})
