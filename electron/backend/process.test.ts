// Task 8.7: the process that runs the backend. It must never be left behind: stop() ends it politely first (the backend watches its stdin and the signal
// asks uvicorn to shut down cleanly), then forcibly after a grace period. What it prints is kept (the last lines) for an error message, with the token removed.
import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { spawn as realSpawn } from 'node:child_process'
import { BackendProcess } from './process.ts'

const TOKEN = 't'.repeat(43)

class FakeStream extends EventEmitter {
  ended = false
  end() { this.ended = true }
}

class FakeChild extends EventEmitter {
  stdin = new FakeStream()
  stdout = new FakeStream()
  stderr = new FakeStream()
  killed: string[] = []
  pid = 4242
  exitCode: number | null = null
  kill(signal: string) { this.killed.push(signal); return true }
  die(code: number | null, signal: string | null = null) { this.exitCode = code; this.emit('exit', code, signal) }
}

function make(extra: Record<string, unknown> = {}) {
  const child = new FakeChild()
  const calls: Array<{ command: string, args: string[], options: Record<string, unknown> }> = []
  const timers: Array<{ fn: () => void, ms: number, cleared: boolean }> = []
  const proc = new BackendProcess({
    command: 'uv', args: ['run', 'x'], cwd: '/repo/backend', env: { A: '1', CLANK_API_TOKEN: TOKEN }, redact: [TOKEN],
    spawn: ((command: string, args: string[], options: Record<string, unknown>) => { calls.push({ command, args, options }); return child }) as never,
    setTimeoutFn: ((fn: () => void, ms: number) => { const t = { fn, ms, cleared: false }; timers.push(t); return t }) as never,
    clearTimeoutFn: ((t: { cleared: boolean }) => { t.cleared = true }) as never,
    killGraceMs: 5000, ...extra,
  })
  return { child, calls, timers, proc }
}

test('start spawns the command with the given env and cwd and pipes for all three streams', () => {
  const { calls, proc } = make()
  proc.start()
  assert.equal(calls.length, 1)
  assert.equal(calls[0].command, 'uv')
  assert.deepEqual(calls[0].args, ['run', 'x'])
  assert.equal(calls[0].options.cwd, '/repo/backend')
  assert.deepEqual(calls[0].options.env, { A: '1', CLANK_API_TOKEN: TOKEN })
  assert.deepEqual(calls[0].options.stdio, ['pipe', 'pipe', 'pipe'], 'stdin stays open: its closing is how the backend learns that we died')
  assert.equal(calls[0].options.shell, undefined, 'never through a shell')
  assert.equal(proc.isAlive(), true)
})

test('start twice does not start a second process', () => {
  const { calls, proc } = make()
  proc.start()
  proc.start()
  assert.equal(calls.length, 1)
})

test('an exit nobody asked for is reported with the code and the last output', () => {
  const { child, proc } = make()
  const seen: unknown[] = []
  proc.on('exit', (e) => seen.push(e))
  proc.start()
  child.stderr.emit('data', Buffer.from('Traceback...\nValueError: boom\n'))
  child.die(1)
  assert.equal(proc.isAlive(), false)
  assert.deepEqual(seen, [{ expected: false, code: 1, signal: null, tail: ['Traceback...', 'ValueError: boom'] }])
})

test('only the last 20 lines are kept, from stdout and stderr together', () => {
  const { child, proc } = make()
  let tail: string[] = []
  proc.on('exit', (e) => { tail = e.tail })
  proc.start()
  child.stdout.emit('data', Buffer.from(Array.from({ length: 30 }, (_, i) => `line ${i}`).join('\n') + '\n'))
  child.die(1)
  assert.equal(tail.length, 20)
  assert.equal(tail[0], 'line 10')
  assert.equal(tail[19], 'line 29')
})

test('a line cut in two chunks is put together again', () => {
  const { child, proc } = make()
  let tail: string[] = []
  proc.on('exit', (e) => { tail = e.tail })
  proc.start()
  child.stderr.emit('data', Buffer.from('half a li'))
  child.stderr.emit('data', Buffer.from('ne\nnext\n'))
  child.die(1)
  assert.deepEqual(tail, ['half a line', 'next'])
})

test('the token is removed from what is kept', () => {
  const { child, proc } = make()
  let tail: string[] = []
  proc.on('exit', (e) => { tail = e.tail })
  proc.start()
  child.stderr.emit('data', Buffer.from(`header x-clank-token: ${TOKEN} sent\n`))
  child.die(1)
  assert.deepEqual(tail, ['header x-clank-token: [token] sent'])
})

test('a command that cannot even start is an exit with a reason, not an exception', () => {
  const { child, proc } = make()
  const seen: Array<{ expected: boolean, code: number | null, tail: string[] }> = []
  proc.on('exit', (e) => seen.push(e))
  proc.start()
  child.emit('error', Object.assign(new Error('spawn uv ENOENT'), { code: 'ENOENT' }))
  assert.equal(seen.length, 1)
  assert.equal(seen[0].expected, false)
  assert.equal(seen[0].code, null)
  assert.match(seen[0].tail.join(' '), /could not start.*ENOENT/)
  assert.equal(proc.isAlive(), false)
})

test('stop closes stdin, asks politely with SIGTERM, and resolves when the process is gone', async () => {
  const { child, proc, timers } = make()
  proc.start()
  const seen: Array<{ expected: boolean }> = []
  proc.on('exit', (e) => seen.push(e))
  const stopped = proc.stop()
  assert.equal(child.stdin.ended, true)
  assert.deepEqual(child.killed, ['SIGTERM'])
  assert.equal(timers.length, 1)
  assert.equal(timers[0].ms, 5000)
  child.die(null, 'SIGTERM')
  await stopped
  assert.equal(timers[0].cleared, true, 'the kill timer is cancelled once it is gone')
  assert.deepEqual(child.killed, ['SIGTERM'], 'no SIGKILL needed')
  assert.deepEqual(seen, [{ expected: true, code: null, signal: 'SIGTERM', tail: [] }])
})

test('a process that ignores the polite request is killed after the grace period', async () => {
  const { child, proc, timers } = make()
  proc.start()
  const stopped = proc.stop()
  timers[0].fn()
  assert.deepEqual(child.killed, ['SIGTERM', 'SIGKILL'])
  child.die(null, 'SIGKILL')
  await stopped
})

test('stop on a process that is not running resolves at once; stop twice shares one stop', async () => {
  const idle = make()
  await idle.proc.stop()
  const { child, proc } = make()
  proc.start()
  const a = proc.stop()
  const b = proc.stop()
  assert.equal(child.killed.length, 1, 'only one SIGTERM')
  child.die(0)
  await Promise.all([a, b])
  await proc.stop()
})

test('killNow is a synchronous SIGKILL for the moment the app is going away', () => {
  const { child, proc } = make()
  proc.start()
  proc.killNow()
  assert.deepEqual(child.killed, ['SIGKILL'])
})

test('after an exit the process can be started again', () => {
  const { child, calls, proc } = make()
  proc.start()
  child.die(0)
  proc.start()
  assert.equal(calls.length, 2)
})

// ---- the real thing

function alive(pid: number) {
  try { process.kill(pid, 0); return true } catch { return false }
}

test('REAL: stop ends a child that watches its stdin, and the pid is gone afterwards', async () => {
  const proc = new BackendProcess({
    command: process.execPath, cwd: process.cwd(), env: {}, redact: [], spawn: realSpawn as never, killGraceMs: 3000,
    args: ['-e', "process.stdin.resume(); process.stdin.on('end', () => process.exit(0)); setInterval(() => {}, 1000)"],
  })
  proc.start()
  const pid = proc.pid!
  assert.equal(alive(pid), true)
  await proc.stop()
  assert.equal(alive(pid), false)
})

test('REAL: a child that ignores everything polite is killed after the grace period', async () => {
  const proc = new BackendProcess({
    command: process.execPath, cwd: process.cwd(), env: {}, redact: [], spawn: realSpawn as never, killGraceMs: 300,
    args: ['-e', "process.on('SIGTERM', () => {}); setInterval(() => {}, 1000)"],
  })
  const exits: Array<{ signal: string | null }> = []
  proc.on('exit', (e) => exits.push(e))
  proc.start()
  await new Promise((r) => setTimeout(r, 300))
  const pid = proc.pid!
  const t0 = Date.now()
  await proc.stop()
  assert.equal(alive(pid), false)
  assert.ok(Date.now() - t0 >= 250, 'it waited for the grace period first')
  assert.equal(exits[0].signal, 'SIGKILL')
})
