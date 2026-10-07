// Streaming answers, step 1.3, part 4: the wiring that cannot be run without Electron itself (the preload script and the main process need the real `electron` module),
// checked by reading the files. What is promised:
// - the preload gives the window exactly three new doors (start, stop, listen) built on the channel names of channels.ts, and never mentions a token, a port or an address;
// - the listener it gives out hands over only the message (never the IPC event), and can be taken off again;
// - the main process builds the stream door from the running backend's target, hands it to the IPC layer, and ends every stream when the backend is not ready;
// - the types the window sees declare the three doors.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = (file: string) => readFileSync(new URL(`../${file}`, import.meta.url), 'utf8')
const preload = read('preload.ts')
const main = read('main.ts')
const types = read('electron-env.d.ts')

test('the preload exposes the three stream doors on top of the channel names', () => {
  assert.match(preload, /startStream: \(id: string, path: string, body\?: object\) => ipcRenderer\.invoke\(CHANNELS\.streamStart,/)
  assert.match(preload, /stopStream: \(id: string\) => ipcRenderer\.invoke\(CHANNELS\.streamStop, \{ id \}\)/)
  assert.match(preload, /onStreamEvents\(listener: \(message: unknown\) => void\)/)
  assert.match(preload, /ipcRenderer\.on\(CHANNELS\.streamEvents, handler\)/)
})

test('the listener hands over only the message, never the IPC event, and can be removed', () => {
  assert.match(preload, /const handler = \(_event: unknown, message: unknown\) => listener\(message\)/)
  assert.match(preload, /ipcRenderer\.off\(CHANNELS\.streamEvents, handler\)/)
})

test('a start sends only the id, the path and the body', () => {
  assert.match(preload, /body === undefined \? \{ id, path \} : \{ id, path, body \}/)
})

test('the preload never mentions a token, a port or an address', () => {
  const streamPart = preload.slice(preload.indexOf('startStream'), preload.indexOf('onStatus(listener'))
  assert.ok(streamPart.length > 100)
  for (const word of [/token/i, /\bport\b/i, /127\.0\.0\.1/, /localhost/i, /http:/i]) assert.doesNotMatch(streamPart, word)
})

test('the main process builds the door from the running backend and hands it to the IPC layer', () => {
  assert.match(main, /new StreamManager\(\{ target: \(\) => running\.target\(\) \}\)/)
  assert.match(main, /registerBackendIpc\(\{[\s\S]*?\bstreams,/)
})

test('every answer being written ends when the backend is not ready (stopped, crashed, restarting or failed)', () => {
  assert.match(main, /if \(status\.state === 'ready'\) void settings\.onBackendReady\(\)[^\n]*\n\s*else streams\?\.abortAll\(\)/)
})

test('the types the window sees declare the three doors', () => {
  assert.match(types, /startStream\(id: string, path: string, body\?: object\): Promise</)
  assert.match(types, /stopStream\(id: string\): Promise</)
  assert.match(types, /onStreamEvents\(listener: \(message: unknown\) => void\): \(\) => void/)
})
