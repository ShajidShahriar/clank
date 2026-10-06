// Task 8.7: the bridge between the window and the backend. Only the app's OWN page may use it (a page that got loaded by mistake, a frame, a navigation
// to another site must not be able to borrow the token through the main process), and what the window sends is checked before anything is forwarded.
import test from 'node:test'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { CHANNELS, isTrustedSender, registerBackendIpc } from './ipc.ts'

const INDEX = '/Applications/Clank.app/Contents/Resources/app.asar/dist/index.html'
const DEV = 'http://localhost:5173/'

test('in development only the dev server origin is trusted', () => {
  const ctx = { devServerUrl: DEV, rendererIndexPath: INDEX }
  assert.equal(isTrustedSender('http://localhost:5173/', ctx), true)
  assert.equal(isTrustedSender('http://localhost:5173/some/page?x=1#y', ctx), true)
  for (const url of ['http://localhost:5174/', 'http://127.0.0.1:5173/', 'https://localhost:5173/', 'http://localhost:5173.evil.com/', 'http://localhost:5173@evil.com/', 'http://localhost:51730/', 'http://evil.com/',
    pathToFileURL(INDEX).href, 'file:///etc/passwd', 'about:blank', '', 'not a url', undefined]) {
    assert.equal(isTrustedSender(url as never, ctx), false, String(url))
  }
})

test('packaged, only the app\'s own index.html is trusted', () => {
  const ctx = { devServerUrl: undefined, rendererIndexPath: INDEX }
  assert.equal(isTrustedSender(pathToFileURL(INDEX).href, ctx), true)
  assert.equal(isTrustedSender(pathToFileURL(INDEX).href + '#/chat', ctx), true)
  for (const url of [pathToFileURL('/Applications/Clank.app/Contents/Resources/app.asar/dist/other.html').href, 'file:///tmp/evil.html', 'http://localhost:5173/',
    'https://evil.com/', 'file:///Applications/Clank.app/Contents/Resources/app.asar/dist/index.html.evil.html']) {
    assert.equal(isTrustedSender(url, ctx), false, url)
  }
})

function setup(trusted = true, pick: () => Promise<unknown> = async () => '/Users/x/project') {
  const handlers = new Map<string, (event: unknown, payload?: unknown) => unknown>()
  const ipcMain = { handle: (channel: string, fn: (event: unknown, payload?: unknown) => unknown) => { handlers.set(channel, fn) } }
  const calls: unknown[][] = []
  let restarts = 0
  const service = {
    request: async (...args: unknown[]) => { calls.push(args); return { ok: true, status: 200, body: { hi: 1 } } },
    status: () => ({ state: 'ready', message: 'ok', detail: [] }),
    restart: async () => { restarts++ },
  }
  let picks = 0
  registerBackendIpc({ ipcMain: ipcMain as never, service: service as never, isTrusted: () => trusted, pickFolder: (async () => { picks++; return pick() }) as never })
  const event = { senderFrame: { url: 'whatever' } }
  return { handlers, calls, event, restarts: () => restarts, picks: () => picks }
}

test('it registers exactly the four channels', () => {
  const { handlers } = setup()
  assert.deepEqual([...handlers.keys()].sort(), [CHANNELS.pickFolder, CHANNELS.request, CHANNELS.restart, CHANNELS.status].sort())
})

test('a trusted request is forwarded with its method, path and body', async () => {
  const { handlers, calls, event } = setup()
  const r = await handlers.get(CHANNELS.request)!(event, { method: 'POST', path: '/projects/1/context', body: { question: 'q' } })
  assert.deepEqual(r, { ok: true, status: 200, body: { hi: 1 } })
  assert.deepEqual(calls, [['POST', '/projects/1/context', { question: 'q' }]])
})

test('a request from a page that is not ours is refused and nothing is forwarded', async () => {
  const { handlers, calls, event, restarts } = setup(false)
  for (const channel of [CHANNELS.request, CHANNELS.status, CHANNELS.restart]) {
    const r = await handlers.get(channel)!(event, { method: 'GET', path: '/health' }) as { ok: boolean, status: number, body: { error: { code: string } } }
    assert.equal(r.ok, false)
    assert.equal(r.body.error.code, 'forbidden')
  }
  assert.equal(calls.length, 0)
  assert.equal(restarts(), 0)
})

test('a payload that is not an object with a string method and path is refused', async () => {
  const { handlers, calls, event } = setup()
  for (const payload of [undefined, null, 'GET /health', 5, [], { method: 5, path: '/health' }, { method: 'GET' }, { path: '/health' }, { method: 'GET', path: {} }]) {
    const r = await handlers.get(CHANNELS.request)!(event, payload) as { body: { error: { code: string } } }
    assert.equal(r.body.error.code, 'bad_request', JSON.stringify(payload))
  }
  assert.equal(calls.length, 0)
})

test('extra fields in the payload are not passed on', async () => {
  const { handlers, calls, event } = setup()
  await handlers.get(CHANNELS.request)!(event, { method: 'GET', path: '/health', port: 1, token: 'x', url: 'http://evil.com', headers: { a: 'b' } })
  assert.deepEqual(calls, [['GET', '/health', undefined]])
})

test('status and restart are forwarded for a trusted page', async () => {
  const { handlers, event, restarts } = setup()
  assert.deepEqual(await handlers.get(CHANNELS.status)!(event), { state: 'ready', message: 'ok', detail: [] })
  await handlers.get(CHANNELS.restart)!(event)
  assert.equal(restarts(), 1)
})

test('a handler that gets no sender frame (a destroyed frame) is refused, not a crash', async () => {
  const handlers = new Map<string, (event: unknown, payload?: unknown) => unknown>()
  registerBackendIpc({
    ipcMain: { handle: (c: string, fn: (e: unknown, p?: unknown) => unknown) => { handlers.set(c, fn) } } as never,
    service: { request: async () => ({ ok: true }) } as never,
    isTrusted: (url: string | undefined) => url === 'ok',
    pickFolder: async () => null,
  })
  const r = await handlers.get(CHANNELS.request)!({ senderFrame: null }, { method: 'GET', path: '/health' }) as { body: { error: { code: string } } }
  assert.equal(r.body.error.code, 'forbidden')
})

// ---- the folder picker (the window adds a project by choosing its folder)

test('a trusted page gets the folder the person chose', async () => {
  const { handlers, event, picks } = setup()
  assert.deepEqual(await handlers.get(CHANNELS.pickFolder)!(event), { ok: true, status: 200, body: { path: '/Users/x/project' } })
  assert.equal(picks(), 1)
})

test('a cancelled dialog is a null path, and so is anything that is not a path', async () => {
  for (const answer of [null, undefined, '', 5, {}, ['/x']]) {
    const { handlers, event } = setup(true, async () => answer)
    assert.deepEqual(await handlers.get(CHANNELS.pickFolder)!(event), { ok: true, status: 200, body: { path: null } }, JSON.stringify(answer))
  }
})

test('a dialog that fails is a fixed error, not an exception and not its message', async () => {
  const { handlers, event } = setup(true, async () => { throw new Error('secret detail from the system') })
  const r = await handlers.get(CHANNELS.pickFolder)!(event) as { ok: boolean, status: number, body: { error: { code: string } } }
  assert.equal(r.ok, false)
  assert.equal(r.body.error.code, 'dialog_failed')
  assert.equal(JSON.stringify(r).includes('secret detail'), false)
})

test('a page that is not ours cannot open a dialog', async () => {
  const { handlers, event, picks } = setup(false)
  const r = await handlers.get(CHANNELS.pickFolder)!(event) as { ok: boolean, body: { error: { code: string } } }
  assert.equal(r.ok, false)
  assert.equal(r.body.error.code, 'forbidden')
  assert.equal(picks(), 0)
})
