// Task 8.7: the bridge between the window and the backend. Only the app's OWN page may use it (a page that got loaded by mistake, a frame, a navigation
// to another site must not be able to borrow the token through the main process), and what the window sends is checked before anything is forwarded.
import test from 'node:test'
import assert from 'node:assert/strict'
import { pathToFileURL } from 'node:url'
import { CHANNELS, isTrustedSender, registerBackendIpc } from './ipc.ts'

const INDEX = '/Applications/Clank.app/Contents/Resources/app.asar/dist/index.html'
const DEV = 'http://localhost:5173/'

/** What an IPC event carries besides the frame: the window (webContents) that asked. */
function fakeSender(id = 7) {
  const sent: Array<{ channel: string, message: unknown }> = []
  const onceHandlers: Array<{ name: string, fn: () => void }> = []
  const onHandlers: Array<{ name: string, fn: (...args: unknown[]) => void }> = []
  const sender = {
    id, destroyed: false, sent, onceHandlers, onHandlers,
    isDestroyed() { return this.destroyed },
    send(channel: string, message: unknown) { sent.push({ channel, message }) },
    once(name: string, fn: () => void) { onceHandlers.push({ name, fn }) },
    on(name: string, fn: (...args: unknown[]) => void) { onHandlers.push({ name, fn }) },
  }
  return { sender }
}

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

function fakeStreams() {
  const calls: Array<{ kind: string, args: unknown[] }> = []
  return {
    calls,
    start: async (...args: unknown[]) => { calls.push({ kind: 'start', args }); return { ok: true, status: 200, body: { id: (args[1] as { id: string }).id } } },
    stop: (...args: unknown[]) => { calls.push({ kind: 'stop', args }); return args[1] === 'known-stream' },
    closeOwner: (...args: unknown[]) => { calls.push({ kind: 'closeOwner', args }) },
  }
}

function setup(trusted = true, pick: () => Promise<unknown> = async () => '/Users/x/project', save: (payload: unknown) => Promise<unknown> = async () => ({ ok: true, status: 200, body: { saved: true } })) {
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
  const saves: unknown[] = []
  const streams = fakeStreams()
  registerBackendIpc({ ipcMain: ipcMain as never, service: service as never, isTrusted: () => trusted, pickFolder: (async () => { picks++; return pick() }) as never, saveLlm: (async (payload: unknown) => { saves.push(payload); return save(payload) }) as never, streams: streams as never })
  const event = { senderFrame: { url: 'whatever' }, ...fakeSender() }
  return { handlers, calls, event, restarts: () => restarts, picks: () => picks, saves, streams }
}

test('it registers exactly the seven channels', () => {
  const { handlers } = setup()
  assert.deepEqual([...handlers.keys()].sort(), [CHANNELS.llmSave, CHANNELS.pickFolder, CHANNELS.request, CHANNELS.restart, CHANNELS.status, CHANNELS.streamStart, CHANNELS.streamStop].sort())
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
    saveLlm: async () => ({}),
    streams: fakeStreams() as never,
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

test('the window may use GET, POST and DELETE only: a PUT is the main process\'s own (the key goes through the settings flow, never the generic bridge)', async () => {
  const { handlers, calls, event } = setup()
  for (const method of ['PUT', 'PATCH', 'put']) {
    const r = await handlers.get(CHANNELS.request)!(event, { method, path: '/settings/llm/key', body: { api_key: 'x' } }) as { ok: boolean, body: { error: { code: string } } }
    assert.equal(r.ok, false, method)
    assert.equal(r.body.error.code, 'bad_request', method)
  }
  assert.equal(calls.length, 0)
  for (const method of ['GET', 'POST', 'DELETE']) await handlers.get(CHANNELS.request)!(event, { method, path: '/health' })
  assert.deepEqual(calls.map((c) => c[0]), ['GET', 'POST', 'DELETE'])
})

// ---- saving the answer-model settings (the key goes through here and nowhere else)

test('a trusted page can save settings and gets the answer of the settings flow', async () => {
  const { handlers, event, saves } = setup()
  const payload = { config: { preset: 'groq', model: 'm' }, apiKey: 'a-key-123' }
  assert.deepEqual(await handlers.get(CHANNELS.llmSave)!(event, payload), { ok: true, status: 200, body: { saved: true } })
  assert.deepEqual(saves, [payload])
})

test('only the config and the key are passed on, nothing else the window adds', async () => {
  const { handlers, event, saves } = setup()
  await handlers.get(CHANNELS.llmSave)!(event, { config: { preset: 'groq' }, apiKey: null, port: 1, token: 'x', url: 'http://evil.com' })
  assert.deepEqual(saves, [{ config: { preset: 'groq' }, apiKey: null }])
})

test('a missing key stays missing (it means: leave the stored key alone)', async () => {
  const { handlers, event, saves } = setup()
  await handlers.get(CHANNELS.llmSave)!(event, { config: { preset: 'groq' } })
  assert.equal('apiKey' in (saves[0] as object), false)
})

test('a page that is not ours cannot save settings or a key', async () => {
  const { handlers, event, saves } = setup(false)
  const r = await handlers.get(CHANNELS.llmSave)!(event, { config: { preset: 'groq' }, apiKey: 'a-key-123' }) as { ok: boolean, body: { error: { code: string } } }
  assert.equal(r.ok, false)
  assert.equal(r.body.error.code, 'forbidden')
  assert.equal(saves.length, 0)
})

test('a payload that is not an object with a config object is refused before the flow is called', async () => {
  const { handlers, event, saves } = setup()
  for (const payload of [undefined, null, 'x', 5, [], { apiKey: 'k' }, { config: 'groq' }, { config: null }, { config: [] }]) {
    const r = await handlers.get(CHANNELS.llmSave)!(event, payload) as { body: { error: { code: string } } }
    assert.equal(r.body.error.code, 'bad_request', JSON.stringify(payload))
  }
  assert.equal(saves.length, 0)
})

test('a flow that throws is a fixed error, never its message (which could hold a key)', async () => {
  const { handlers, event } = setup(true, undefined, async () => { throw new Error('secret gsk_abc123 detail') })
  const r = await handlers.get(CHANNELS.llmSave)!(event, { config: { preset: 'groq' }, apiKey: 'gsk_abc123' }) as { ok: boolean, body: { error: { code: string } } }
  assert.equal(r.ok, false)
  assert.equal(r.body.error.code, 'settings_failed')
  assert.equal(JSON.stringify(r).includes('gsk_abc123'), false)
})


// ---- answers written live (the stream door)

test('the stream channels have their own names', () => {
  assert.equal(CHANNELS.streamStart, 'clank:stream-start')
  assert.equal(CHANNELS.streamStop, 'clank:stream-stop')
  assert.equal(CHANNELS.streamEvents, 'clank:stream-events')
})

test('a trusted page starts a stream: only the id, the path and the body are passed on, with the window that asked', async () => {
  const { handlers, event, streams } = setup()
  const r = await handlers.get(CHANNELS.streamStart)!(event, { id: 'abcd1234', path: '/projects/1/answer/stream', body: { question: 'q' }, port: 1, token: 'x', url: 'http://evil.com', ownerId: 99, headers: { a: 'b' } })
  assert.deepEqual(r, { ok: true, status: 200, body: { id: 'abcd1234' } })
  assert.equal(streams.calls.length, 1)
  const [owner, request] = streams.calls[0].args as [{ id: number, send: unknown }, Record<string, unknown>]
  assert.equal(owner.id, 7, 'the window is the one that asked, whatever the payload says')
  assert.deepEqual(request, { id: 'abcd1234', path: '/projects/1/answer/stream', body: { question: 'q' } })
})

test('a stream may be started with no body', async () => {
  const { handlers, event, streams } = setup()
  await handlers.get(CHANNELS.streamStart)!(event, { id: 'abcd1234', path: '/projects/1/answer/stream' })
  assert.equal((streams.calls[0].args[1] as { body?: unknown }).body, undefined)
})

test('the events for a window are sent to THAT window on the events channel', async () => {
  const { handlers, event, streams } = setup()
  await handlers.get(CHANNELS.streamStart)!(event, { id: 'abcd1234', path: '/projects/1/answer/stream', body: {} })
  const owner = streams.calls[0].args[0] as { send(message: unknown): void }
  owner.send({ id: 'abcd1234', events: [{ type: 'delta', text: 'a' }] })
  assert.deepEqual((event.sender as { sent: unknown[] }).sent, [{ channel: CHANNELS.streamEvents, message: { id: 'abcd1234', events: [{ type: 'delta', text: 'a' }] } }])
})

test('a window that is already destroyed cannot be written to: send throws, so the stream ends', async () => {
  const { handlers, event, streams } = setup()
  await handlers.get(CHANNELS.streamStart)!(event, { id: 'abcd1234', path: '/projects/1/answer/stream', body: {} })
  ;(event.sender as { destroyed: boolean }).destroyed = true
  const owner = streams.calls[0].args[0] as { send(message: unknown): void }
  assert.throws(() => owner.send({ id: 'abcd1234', events: [] }))
  assert.equal((event.sender as { sent: unknown[] }).sent.length, 0)
})

test('when the window is destroyed its streams are closed, and the listener is added only once per window', async () => {
  const { handlers, event, streams } = setup()
  for (const id of ['abcd1234', 'efgh5678', 'ijkl9012']) await handlers.get(CHANNELS.streamStart)!(event, { id, path: '/projects/1/answer/stream', body: {} })
  const handlersOnce = (event.sender as { onceHandlers: Array<{ name: string, fn: () => void }> }).onceHandlers
  assert.equal(handlersOnce.length, 1)
  assert.equal(handlersOnce[0].name, 'destroyed')
  handlersOnce[0].fn()
  assert.deepEqual(streams.calls.filter((c) => c.kind === 'closeOwner').map((c) => c.args), [[7]])
})

test('a page that is not ours cannot start or stop a stream', async () => {
  const { handlers, event, streams } = setup(false)
  for (const channel of [CHANNELS.streamStart, CHANNELS.streamStop]) {
    const r = await handlers.get(channel)!(event, { id: 'abcd1234', path: '/projects/1/answer/stream', body: {} }) as { ok: boolean, body: { error: { code: string } } }
    assert.equal(r.ok, false)
    assert.equal(r.body.error.code, 'forbidden')
  }
  assert.equal(streams.calls.length, 0)
})

test('a stream payload that is not an object with a string id and path is refused before the door is called', async () => {
  const { handlers, event, streams } = setup()
  const good = { id: 'abcd1234', path: '/projects/1/answer/stream', body: {} }
  for (const payload of [undefined, null, 'x', 5, [], { ...good, id: 5 }, { ...good, id: undefined }, { ...good, path: 5 }, { ...good, path: undefined }, { ...good, body: [] }, { ...good, body: 'text' }, { ...good, body: 5 }]) {
    const r = await handlers.get(CHANNELS.streamStart)!(event, payload) as { body: { error: { code: string } } }
    assert.equal(r.body.error.code, 'bad_request', JSON.stringify(payload))
  }
  assert.equal(streams.calls.length, 0)
})

test('an event with no window behind it (no sender) cannot start a stream', async () => {
  const { handlers, streams } = setup()
  const r = await handlers.get(CHANNELS.streamStart)!({ senderFrame: { url: 'x' } }, { id: 'abcd1234', path: '/projects/1/answer/stream', body: {} }) as { body: { error: { code: string } } }
  assert.equal(r.body.error.code, 'bad_request')
  assert.equal(streams.calls.length, 0)
})

test('a door that throws is a fixed error, never its message', async () => {
  const { handlers, event, streams } = setup()
  streams.start = async () => { throw new Error('secret token abc at port 4321') }
  const r = await handlers.get(CHANNELS.streamStart)!(event, { id: 'abcd1234', path: '/projects/1/answer/stream', body: {} }) as { ok: boolean, body: { error: { code: string } } }
  assert.equal(r.ok, false)
  assert.equal(r.body.error.code, 'stream_failed')
  assert.equal(JSON.stringify(r).includes('secret'), false)
})

test('stop is for the window that asked, with the id, and says whether anything was stopped', async () => {
  const { handlers, event, streams } = setup()
  assert.deepEqual(await handlers.get(CHANNELS.streamStop)!(event, { id: 'known-stream', ownerId: 99, extra: 1 }), { ok: true, status: 200, body: { stopped: true } })
  assert.deepEqual(await handlers.get(CHANNELS.streamStop)!(event, { id: 'unknown-one' }), { ok: true, status: 200, body: { stopped: false } })
  assert.deepEqual(streams.calls.map((c) => c.args), [[7, 'known-stream'], [7, 'unknown-one']], 'the window id comes from the sender, never from the payload')
})

test('a stop payload without a string id is refused', async () => {
  const { handlers, event, streams } = setup()
  for (const payload of [undefined, null, 'x', 5, [], {}, { id: 5 }, { id: null }]) {
    const r = await handlers.get(CHANNELS.streamStop)!(event, payload) as { body: { error: { code: string } } }
    assert.equal(r.body.error.code, 'bad_request', JSON.stringify(payload))
  }
  assert.equal(streams.calls.length, 0)
})

test('a stop from an event with no window behind it is refused', async () => {
  const { handlers, streams } = setup()
  const r = await handlers.get(CHANNELS.streamStop)!({ senderFrame: { url: 'x' } }, { id: 'known-stream' }) as { body: { error: { code: string } } }
  assert.equal(r.body.error.code, 'bad_request')
  assert.equal(streams.calls.length, 0)
})


test('a page that reloads or navigates away, or whose renderer crashes, loses its streams (they would otherwise go on paying for an answer nobody can read)', async () => {
  const { handlers, event, streams } = setup()
  await handlers.get(CHANNELS.streamStart)!(event, { id: 'abcd1234', path: '/projects/1/answer/stream', body: {} })
  const on = (event.sender as { onHandlers: Array<{ name: string, fn: (...args: unknown[]) => void }> }).onHandlers
  const closes = () => streams.calls.filter((c) => c.kind === 'closeOwner').length
  const navigation = on.find((h) => h.name === 'did-start-navigation')!
  assert.ok(navigation, 'the window is watched for navigation')
  navigation.fn({ isMainFrame: false, isSameDocument: false })
  navigation.fn({ isMainFrame: true, isSameDocument: true })
  assert.equal(closes(), 0, 'a sub-frame or an in-page change is not a reload')
  navigation.fn({ isMainFrame: true, isSameDocument: false })
  assert.equal(closes(), 1)
  const crash = on.find((h) => h.name === 'render-process-gone')!
  assert.ok(crash)
  crash.fn({}, { reason: 'crashed' })
  assert.equal(closes(), 2)
})

test('the window is watched once, not once per stream', async () => {
  const { handlers, event } = setup()
  for (const id of ['abcd1234', 'efgh5678']) await handlers.get(CHANNELS.streamStart)!(event, { id, path: '/projects/1/answer/stream', body: {} })
  const on = (event.sender as { onHandlers: Array<{ name: string }> }).onHandlers
  assert.equal(on.filter((h) => h.name === 'did-start-navigation').length, 1)
  assert.equal(on.filter((h) => h.name === 'render-process-gone').length, 1)
})
