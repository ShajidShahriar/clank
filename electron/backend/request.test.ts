// Task 8.7: the only way the window reaches the backend is through the main process, which holds the token. The window may only name a method, a path and a
// JSON body; the address, the port and the token are never its to choose, and the path is checked against a strict shape.
import test from 'node:test'
import assert from 'node:assert/strict'
import { buildBackendRequest, callBackend } from './request.ts'

const TOKEN = 't'.repeat(43)
const base = { port: 8123, token: TOKEN }

test('a GET becomes a request to 127.0.0.1 on the backend port with the token in the header', () => {
  const r = buildBackendRequest({ ...base, method: 'GET', path: '/health' })
  assert.equal(r.url, 'http://127.0.0.1:8123/health')
  assert.equal(r.init.method, 'GET')
  assert.equal(r.init.headers['x-clank-token'], TOKEN)
  assert.equal(r.init.body, undefined)
  assert.equal(r.init.redirect, 'error', 'a redirect is never followed: the token must not travel anywhere else')
})

test('a POST carries its object as JSON', () => {
  const r = buildBackendRequest({ ...base, method: 'POST', path: '/projects/3/context', body: { question: 'how?', k: 5 } })
  assert.equal(r.init.body, '{"question":"how?","k":5}')
  assert.equal(r.init.headers['content-type'], 'application/json')
})

test('a POST with no body sends none', () => {
  assert.equal(buildBackendRequest({ ...base, method: 'POST', path: '/projects/1/index' }).init.body, undefined)
})

for (const path of ['/health', '/startup-sync', '/projects/1/index', '/projects/12/index/cancel', '/projects/3/context']) {
  test(`the path ${path} is accepted`, () => {
    assert.doesNotThrow(() => buildBackendRequest({ ...base, method: 'GET', path }))
  })
}

for (const path of ['', '/', 'health', '//evil.com/x', '/a/../b', '/..', '/a/./b', '/a?x=1', '/a#b', 'http://evil.com/', '/a b', '/a\n', '/%2e%2e/x', '/a%2fb',
  '/projects/1/index/', '/a//b', '\\evil', '/a\\b', '/é', '/a;b', ' /health', '/health ']) {
  test(`the path ${JSON.stringify(path)} is refused`, () => {
    assert.throws(() => buildBackendRequest({ ...base, method: 'GET', path }), /path/)
  })
}

test('only GET and POST, in capitals', () => {
  for (const method of ['DELETE', 'PUT', 'PATCH', 'get', 'post', 'OPTIONS', 'HEAD', '', 'GET ']) {
    assert.throws(() => buildBackendRequest({ ...base, method, path: '/health' }), /method/)
  }
})

test('a GET cannot carry a body', () => {
  assert.throws(() => buildBackendRequest({ ...base, method: 'GET', path: '/health', body: { a: 1 } }), /body/)
})

test('a body must be a plain object that turns into JSON and is not huge', () => {
  const circular: Record<string, unknown> = {}
  circular.self = circular
  for (const body of ['text', 5, null, [1, 2], circular, { n: 1n }, { text: 'x'.repeat(70000) }]) {
    assert.throws(() => buildBackendRequest({ ...base, method: 'POST', path: '/projects/1/context', body: body as never }), /body/)
  }
})

test('a port outside 1 to 65535 is refused', () => {
  for (const port of [0, -1, 65536, 1.5, NaN]) {
    assert.throws(() => buildBackendRequest({ port, token: TOKEN, method: 'GET', path: '/health' }), /port/)
  }
})

test('an error never contains the token', () => {
  assert.throws(() => buildBackendRequest({ ...base, method: 'GET', path: `/${TOKEN}?x` }), (e: Error) => !e.message.includes(TOKEN))
})

// ---- calling

function reply(status: number, body: unknown) {
  return { status, text: async () => (typeof body === 'string' ? body : JSON.stringify(body)) }
}

test('a good answer comes back with its status and parsed body', async () => {
  const r = await callBackend({ ...base, method: 'GET', path: '/health', fetchFn: (async () => reply(200, { status: 'ok' })) as never })
  assert.deepEqual(r, { ok: true, status: 200, body: { status: 'ok' } })
})

test('an error answer keeps its status and its JSON error', async () => {
  const body = { error: { code: 'not_indexed', message: 'Index it first.' } }
  const r = await callBackend({ ...base, method: 'POST', path: '/projects/1/context', body: { question: 'q' }, fetchFn: (async () => reply(409, body)) as never })
  assert.deepEqual(r, { ok: false, status: 409, body })
})

test('an answer that is not JSON gives a null body, not a crash', async () => {
  const r = await callBackend({ ...base, method: 'GET', path: '/health', fetchFn: (async () => reply(502, '<html>bad gateway</html>')) as never })
  assert.deepEqual(r, { ok: false, status: 502, body: null })
})

test('when the backend cannot be reached the answer is a fixed error with status 0', async () => {
  const r = await callBackend({ ...base, method: 'GET', path: '/health', fetchFn: (async () => { throw new Error(`connect ECONNREFUSED 127.0.0.1:8123 token ${TOKEN}`) }) as never })
  assert.equal(r.ok, false)
  assert.equal(r.status, 0)
  assert.deepEqual(r.body, { error: { code: 'backend_unavailable', message: 'The Clank backend is not reachable.' } })
  assert.equal(JSON.stringify(r).includes(TOKEN), false)
})

test('a request that takes too long is cut off with its own error', async () => {
  let signal: AbortSignal | undefined
  const r = await callBackend({
    ...base, method: 'GET', path: '/health', timeoutMs: 20,
    fetchFn: ((_url: string, init: { signal: AbortSignal }) => new Promise((_resolve, reject) => {
      signal = init.signal
      init.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'TimeoutError' })))
    })) as never,
  })
  assert.ok(signal)
  assert.deepEqual(r.body, { error: { code: 'backend_timeout', message: 'The Clank backend took too long to answer.' } })
  assert.equal(r.status, 0)
})

test('a bad request from the window is answered with a fixed error and nothing is sent', async () => {
  let called = false
  const r = await callBackend({ ...base, method: 'GET', path: '/a?x=1', fetchFn: (async () => { called = true; return reply(200, {}) }) as never })
  assert.equal(called, false)
  assert.equal(r.status, 0)
  assert.deepEqual(r.body, { error: { code: 'bad_request', message: 'The request was not allowed.' } })
})
