// Task 8.7: the whole life of the backend as the app sees it: starting -> ready -> (crashed | stopped), or starting -> failed. What is promised:
// every request carries the token, nothing is sent while the backend is not ready, a failed or stopped start never leaves a process behind, a restart uses a
// NEW port and a NEW token, and neither the token nor the command line's secrets ever show up in a status.
import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { BackendService, type BackendStatus } from './service.ts'

class FakeStream extends EventEmitter {
  ended = false
  end() { this.ended = true }
}

class FakeChild extends EventEmitter {
  stdin = new FakeStream()
  stdout = new FakeStream()
  stderr = new FakeStream()
  killed: string[] = []
  pid = 100
  autoDie = true
  kill(signal: string) {
    this.killed.push(signal)
    if (this.autoDie) queueMicrotask(() => this.die(null, signal))
    return true
  }
  die(code: number | null, signal: string | null = null) { this.emit('exit', code, signal) }
}

type Harness = ReturnType<typeof harness>

function harness(extra: Record<string, unknown> = {}) {
  const children: FakeChild[] = []
  const spawned: Array<{ command: string, args: string[], env: Record<string, string>, cwd: string }> = []
  const fetched: Array<{ url: string, method: string, token: string | undefined, body?: string }> = []
  let port = 4000
  let n = 0
  let now = 0
  const state = { healthy: true, healthStatus: 200 }
  const service = new BackendService({
    userDataPath: '/Users/x/Library/Application Support/Clank', appRoot: '/repo', resourcesPath: '/res', packaged: false, platform: 'darwin',
    baseEnv: { PATH: '/bin', CLANK_API_TOKEN: 'parent-token' },
    spawn: ((command: string, args: string[], options: { env: Record<string, string>, cwd: string }) => {
      spawned.push({ command, args, env: options.env, cwd: options.cwd })
      const child = new FakeChild()
      children.push(child)
      return child
    }) as never,
    findPort: async () => ++port,
    makeToken: () => `token-${++n}-`.padEnd(43, 'x'),
    fetchFn: (async (url: string, init: { method: string, headers: Record<string, string>, body?: string }) => {
      fetched.push({ url, method: init.method, token: init.headers['x-clank-token'], body: init.body })
      if (!state.healthy) throw new Error('ECONNREFUSED')
      const body = url.endsWith('/health') ? { status: 'ok' } : { echoed: url }
      return { status: url.endsWith('/health') ? state.healthStatus : 200, text: async () => JSON.stringify(body), json: async () => body }
    }) as never,
    now: () => now,
    sleep: async (ms: number) => { now += ms; await new Promise((r) => setImmediate(r)) },
    healthTimeoutMs: 2000, healthIntervalMs: 250, killGraceMs: 50,
    ...extra,
  })
  const statuses: BackendStatus[] = []
  service.onStatus((s) => statuses.push(s))
  return { service, children, spawned, fetched, statuses, state }
}

const states = (h: Harness) => h.statuses.map((s) => s.state)

test('start goes starting -> ready, with the free port, a fresh token and the data folder', async () => {
  const h = harness()
  assert.equal(h.service.status().state, 'stopped')
  await h.service.start()
  assert.deepEqual(states(h), ['starting', 'ready'])
  assert.equal(h.service.status().state, 'ready')
  assert.equal(h.spawned.length, 1)
  const s = h.spawned[0]
  assert.equal(s.command, 'uv')
  assert.deepEqual(s.args, ['run', 'python', 'run.py', '--port', '4001', '--exit-when-stdin-closes'])
  assert.equal(s.cwd, '/repo/backend')
  assert.equal(s.env.CLANK_DATA_DIR, '/Users/x/Library/Application Support/Clank/backend')
  assert.equal(s.env.CLANK_API_TOKEN, 'token-1-'.padEnd(43, 'x'), "the parent's own token setting is replaced")
  assert.deepEqual(h.fetched[0], { url: 'http://127.0.0.1:4001/health', method: 'GET', token: 'token-1-'.padEnd(43, 'x'), body: undefined })
})

test('packaged, it runs the executable from the app resources', async () => {
  const h = harness({ packaged: true, resourcesPath: '/Applications/Clank.app/Contents/Resources' })
  await h.service.start()
  assert.equal(h.spawned[0].command, '/Applications/Clank.app/Contents/Resources/backend/clank-backend')
})

test('start twice at once is one start', async () => {
  const h = harness()
  await Promise.all([h.service.start(), h.service.start()])
  await h.service.start()
  assert.equal(h.spawned.length, 1)
})

test('a request while ready goes to the backend with the token and comes back parsed', async () => {
  const h = harness()
  await h.service.start()
  const r = await h.service.request('POST', '/projects/3/context', { question: 'how?' })
  assert.deepEqual(r, { ok: true, status: 200, body: { echoed: 'http://127.0.0.1:4001/projects/3/context' } })
  const sent = h.fetched.at(-1)!
  assert.equal(sent.token, 'token-1-'.padEnd(43, 'x'))
  assert.equal(sent.body, '{"question":"how?"}')
})

test('nothing is sent while the backend is not ready', async () => {
  const h = harness()
  const unavailable = { ok: false, status: 0, body: { error: { code: 'backend_unavailable', message: 'The Clank backend is not running.' } } }
  assert.deepEqual(await h.service.request('GET', '/health'), unavailable, 'before start')
  await h.service.start()
  await h.service.stop()
  assert.deepEqual(await h.service.request('GET', '/health'), unavailable, 'after stop')
  assert.equal(h.fetched.filter((f) => !f.url.endsWith('/health') || f.method !== 'GET').length, 0)
  assert.equal(h.fetched.length, 1, 'only the health check of the start')
})

test('a process that dies while starting makes the start FAILED, with its last output', async () => {
  const h = harness()
  h.state.healthy = false
  setImmediate(() => { h.children[0].stderr.emit('data', Buffer.from('ModuleNotFoundError: No module named chromadb\n')); h.children[0].die(1) })
  await h.service.start()
  const s = h.service.status()
  assert.equal(s.state, 'failed')
  assert.equal(s.message, 'The Clank backend could not start.')
  assert.match(s.detail.join('\n'), /ModuleNotFoundError/)
  assert.deepEqual(states(h), ['starting', 'failed'])
})

test('a backend that never answers is given up on and its process is stopped (no orphan)', async () => {
  const h = harness()
  h.state.healthy = false
  await h.service.start()
  assert.equal(h.service.status().state, 'failed')
  assert.match(h.service.status().detail.join(' '), /did not become ready within 2 seconds/)
  assert.deepEqual(h.children[0].killed, ['SIGTERM'], 'the process it started was stopped')
  assert.equal(h.children[0].stdin.ended, true)
})

test('a command that does not exist is a failed start that says why', async () => {
  const h = harness()
  h.state.healthy = false
  setImmediate(() => h.children[0].emit('error', new Error('spawn uv ENOENT')))
  await h.service.start()
  assert.equal(h.service.status().state, 'failed')
  assert.match(h.service.status().detail.join(' '), /could not start.*ENOENT/)
})

test('no free port is a failed start and nothing is spawned', async () => {
  const h = harness({ findPort: async () => { throw new Error('no sockets') } })
  await h.service.start()
  assert.equal(h.service.status().state, 'failed')
  assert.equal(h.spawned.length, 0)
  assert.match(h.service.status().detail.join(' '), /no sockets/)
})

test('an exit nobody asked for while running is CRASHED, and requests are refused from then on', async () => {
  const h = harness()
  await h.service.start()
  h.children[0].stderr.emit('data', Buffer.from('RuntimeError: out of memory\n'))
  h.children[0].die(137)
  assert.equal(h.service.status().state, 'crashed')
  assert.equal(h.service.status().message, 'The Clank backend stopped unexpectedly.')
  assert.match(h.service.status().detail.join(' '), /out of memory/)
  assert.deepEqual(states(h), ['starting', 'ready', 'crashed'])
  assert.equal((await h.service.request('GET', '/health')).status, 0)
})

test('stop ends the process politely and the status is stopped, not crashed', async () => {
  const h = harness()
  await h.service.start()
  await h.service.stop()
  assert.deepEqual(h.children[0].killed, ['SIGTERM'])
  assert.equal(h.children[0].stdin.ended, true)
  assert.deepEqual(states(h), ['starting', 'ready', 'stopped'])
})

test('stop while it is still starting leaves it stopped and the process ended', async () => {
  const h = harness()
  h.state.healthy = false
  const starting = h.service.start()
  await new Promise((r) => setImmediate(r))
  await h.service.stop()
  await starting
  assert.equal(h.service.status().state, 'stopped')
  assert.deepEqual(h.children[0].killed, ['SIGTERM'])
  assert.equal(states(h).includes('failed'), false, 'a stop is not a failure')
})

test('restart stops the old process first and then starts one with a NEW port and a NEW token', async () => {
  const h = harness()
  await h.service.start()
  await h.service.restart()
  assert.equal(h.spawned.length, 2)
  assert.deepEqual(h.children[0].killed, ['SIGTERM'])
  assert.notEqual(h.spawned[0].env.CLANK_API_TOKEN, h.spawned[1].env.CLANK_API_TOKEN)
  assert.deepEqual(h.spawned[1].args.slice(3, 5), ['--port', '4002'])
  assert.equal(h.service.status().state, 'ready')
  await h.service.request('GET', '/health')
  assert.equal(h.fetched.at(-1)!.token, 'token-2-'.padEnd(43, 'x'), 'requests use the new token')
})

test('a crashed backend can be started again', async () => {
  const h = harness()
  await h.service.start()
  h.children[0].die(1)
  await h.service.restart()
  assert.equal(h.service.status().state, 'ready')
  assert.equal(h.spawned.length, 2)
})

test('killNow is a synchronous SIGKILL of the running process', async () => {
  const h = harness()
  await h.service.start()
  h.children[0].autoDie = false
  h.service.killNow()
  assert.deepEqual(h.children[0].killed, ['SIGKILL'])
})

test('the token never appears in a status, in any state', async () => {
  const h = harness()
  h.state.healthy = false
  setImmediate(() => { h.children[0].stderr.emit('data', Buffer.from(`sent header x-clank-token: ${'token-1-'.padEnd(43, 'x')}\n`)); h.children[0].die(1) })
  await h.service.start()
  assert.equal(JSON.stringify(h.statuses).includes('token-1-'), false)
  assert.match(JSON.stringify(h.statuses), /\[token\]/)
})

test('a listener that throws does not stop the others or the service', async () => {
  const h = harness()
  h.service.onStatus(() => { throw new Error('bad listener') })
  const seen: string[] = []
  h.service.onStatus((s) => seen.push(s.state))
  await h.service.start()
  assert.deepEqual(seen, ['starting', 'ready'])
})

test('a listener can be removed', async () => {
  const h = harness()
  const seen: string[] = []
  const off = h.service.onStatus((s) => seen.push(s.state))
  off()
  await h.service.start()
  assert.deepEqual(seen, [])
})
