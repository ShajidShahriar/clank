import test from 'node:test'
import assert from 'node:assert/strict'
import net from 'node:net'
import { findFreePort } from './port.ts'

test('it returns a port nobody is listening on, on 127.0.0.1', async () => {
  const port = await findFreePort()
  assert.ok(Number.isInteger(port) && port > 0 && port < 65536)
  await new Promise<void>((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(port, '127.0.0.1', () => server.close(() => resolve()))
  })
})

test('two calls in a row do not hand out a port that is in use', async () => {
  const busy = net.createServer()
  await new Promise<void>((r) => busy.listen(0, '127.0.0.1', () => r()))
  const busyPort = (busy.address() as net.AddressInfo).port
  try {
    for (let i = 0; i < 5; i++) assert.notEqual(await findFreePort(), busyPort)
  } finally {
    busy.close()
  }
})

test('it gives the port back (the server it used is closed)', async () => {
  let closed = false
  const hosts: string[] = []
  const fake = () => {
    const handlers: Record<string, (...a: unknown[]) => void> = {}
    const server = {
      once(name: string, fn: (...a: unknown[]) => void) { handlers[name] = fn; return server },
      listen(_port: number, host: string, cb: () => void) { hosts.push(host); cb(); return server },
      address() { return { port: 4321 } },
      close(cb?: () => void) { closed = true; cb?.(); return server },
    }
    return server
  }
  assert.equal(await findFreePort(fake as never), 4321)
  assert.equal(closed, true)
  assert.deepEqual(hosts, ['127.0.0.1'], 'the probe listens on the loopback address only')
})

test('a failure to listen is an error, not a hang', async () => {
  const broken = () => {
    const server = {
      once(name: string, fn: (e: Error) => void) { if (name === 'error') setImmediate(() => fn(new Error('no sockets'))); return server },
      listen() { return server },
      address() { return null },
      close() { return server },
    }
    return server
  }
  await assert.rejects(findFreePort(broken as never), /no sockets/)
})
