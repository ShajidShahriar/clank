// Task 8.7: waiting for the backend to answer /health. It sends the token (every request needs it), gives up when the process dies or the time is up,
// and never puts the token into an error.
import test from 'node:test'
import assert from 'node:assert/strict'
import { waitForHealth } from './health.ts'

const TOKEN = 't'.repeat(43)

function clock() {
  let now = 0
  return { now: () => now, sleep: async (ms: number) => { now += ms } }
}
const ok = () => ({ status: 200, json: async () => ({ status: 'ok', embedder: 'ready' }) })

test('it returns as soon as /health answers ok, with the token in the header', async () => {
  const c = clock()
  const seen: Array<{ url: string, token: string | undefined }> = []
  await waitForHealth({
    url: 'http://127.0.0.1:8123/health', token: TOKEN, isAlive: () => true, ...c,
    fetchFn: (async (url: string, init: { method: string, headers: Record<string, string> }) => { assert.equal(init.method, 'GET'); seen.push({ url, token: init.headers['x-clank-token'] }); return ok() }) as never,
  })
  assert.deepEqual(seen, [{ url: 'http://127.0.0.1:8123/health', token: TOKEN }])
})

test('it tries again while the connection is refused (the server is still starting)', async () => {
  const c = clock()
  let calls = 0
  await waitForHealth({
    url: 'http://127.0.0.1:1/health', token: TOKEN, isAlive: () => true, intervalMs: 250, ...c,
    fetchFn: (async () => { calls++; if (calls < 4) throw new Error('ECONNREFUSED'); return ok() }) as never,
  })
  assert.equal(calls, 4)
  assert.equal(c.now(), 750, 'it waited the interval between tries')
})

test('a 401 or a 500 is not "healthy"', async () => {
  const c = clock()
  let calls = 0
  await waitForHealth({
    url: 'u', token: TOKEN, isAlive: () => true, ...c,
    fetchFn: (async () => { calls++; return calls === 1 ? { status: 401, json: async () => ({ status: 'ok' }) } : calls === 2 ? { status: 500, json: async () => ({ status: 'ok' }) } : ok() }) as never,
  })
  assert.equal(calls, 3)
})

test('a 200 whose body is not {status: "ok"} is not healthy either', async () => {
  const c = clock()
  let calls = 0
  await waitForHealth({
    url: 'u', token: TOKEN, isAlive: () => true, ...c,
    fetchFn: (async () => { calls++; return calls === 1 ? { status: 200, json: async () => ({ hello: 1 }) } : calls === 2 ? { status: 200, json: async () => { throw new Error('not json') } } : ok() }) as never,
  })
  assert.equal(calls, 3)
})

test('it gives up after the time limit and says the last problem', async () => {
  const c = clock()
  await assert.rejects(
    waitForHealth({ url: 'u', token: TOKEN, isAlive: () => true, timeoutMs: 1000, intervalMs: 250, ...c, fetchFn: (async () => { throw new Error('ECONNREFUSED') }) as never }),
    /did not become ready within 1 seconds.*ECONNREFUSED/s,
  )
})

test('it stops at once when the process has died, without waiting for the time limit', async () => {
  const c = clock()
  let alive = true
  let calls = 0
  await assert.rejects(
    waitForHealth({
      url: 'u', token: TOKEN, timeoutMs: 30000, ...c, isAlive: () => alive,
      fetchFn: (async () => { calls++; if (calls === 2) alive = false; throw new Error('ECONNREFUSED') }) as never,
    }),
    /stopped before it was ready/,
  )
  assert.ok(c.now() < 1000, 'it did not wait out the limit')
})

test('the token never appears in an error', async () => {
  const c = clock()
  await assert.rejects(
    waitForHealth({ url: 'u', token: TOKEN, isAlive: () => true, timeoutMs: 500, ...c, fetchFn: (async () => { throw new Error(`failed with ${TOKEN}`) }) as never }),
    (e: Error) => !e.message.includes(TOKEN),
  )
})

test('with no limit given it waits a full minute: the first start of a freshly installed backend is scanned by the system and took 11 to 21 seconds', async () => {
  const c = clock()
  await assert.rejects(
    waitForHealth({ url: 'u', token: TOKEN, isAlive: () => true, ...c, fetchFn: (async () => { throw new Error('ECONNREFUSED') }) as never }),
    /did not become ready within 60 seconds/,
  )
  assert.ok(c.now() >= 60000 && c.now() < 61000)
})
