// Task 8.7: what the backend is started with. The token and the data folder travel in the ENVIRONMENT (a command line is visible to every process on the
// machine through `ps`), and nothing from the parent's CLANK_* settings may leak through.
import test from 'node:test'
import assert from 'node:assert/strict'
import { backendCommand, buildBackendEnv, dataDirFor, makeToken } from './env.ts'

const TOKEN = 'a'.repeat(43)

test('a token is 32 random bytes in base64url: 43 URL-safe characters, never the same twice', () => {
  const a = makeToken()
  const b = makeToken()
  assert.match(a, /^[A-Za-z0-9_-]{43}$/)
  assert.notEqual(a, b)
})

test('the token comes from the random source it is given (so the test can see the size asked for)', () => {
  let asked = 0
  const token = makeToken((n: number) => { asked = n; return Buffer.alloc(n, 7) })
  assert.equal(asked, 32)
  assert.equal(token, Buffer.alloc(32, 7).toString('base64url'))
})

test('the data folder is a "backend" folder inside the user-data folder', () => {
  assert.equal(dataDirFor('/Users/x/Library/Application Support/Clank'), '/Users/x/Library/Application Support/Clank/backend')
  assert.throws(() => dataDirFor('relative/path'), /absolute/)
  assert.throws(() => dataDirFor(''), /absolute/)
})

test('the environment has the data folder, the token, no allowed origins and unbuffered output', () => {
  const env = buildBackendEnv({ baseEnv: { PATH: '/usr/bin', HOME: '/Users/x' }, dataDir: '/data/backend', token: TOKEN })
  assert.equal(env.CLANK_DATA_DIR, '/data/backend')
  assert.equal(env.CLANK_API_TOKEN, TOKEN)
  assert.equal(env.CLANK_ALLOWED_ORIGINS, '', 'the window never calls the backend directly, so no origin is allowed')
  assert.equal(env.PYTHONUNBUFFERED, '1')
  assert.equal(env.PATH, '/usr/bin')
  assert.equal(env.HOME, '/Users/x')
})

test('settings of the parent process cannot leak into the backend', () => {
  const env = buildBackendEnv({
    baseEnv: { CLANK_DATA_DIR: '/elsewhere', CLANK_API_TOKEN: 'old-token', CLANK_ALLOWED_ORIGINS: '*', CLANK_OTHER: 'x', PATH: '/bin' },
    dataDir: '/data/backend', token: TOKEN,
  })
  assert.equal(env.CLANK_DATA_DIR, '/data/backend')
  assert.equal(env.CLANK_API_TOKEN, TOKEN)
  assert.equal(env.CLANK_ALLOWED_ORIGINS, '')
  assert.equal('CLANK_OTHER' in env, false)
})

test('undefined values are dropped and the input is not changed', () => {
  const base = { PATH: '/bin', GONE: undefined }
  const env = buildBackendEnv({ baseEnv: base, dataDir: '/d', token: TOKEN })
  assert.equal('GONE' in env, false)
  assert.deepEqual(base, { PATH: '/bin', GONE: undefined })
})

test('a relative data folder or a short token is refused (the backend would refuse it too)', () => {
  assert.throws(() => buildBackendEnv({ baseEnv: {}, dataDir: 'data', token: TOKEN }), /absolute/)
  assert.throws(() => buildBackendEnv({ baseEnv: {}, dataDir: '/d', token: 'short' }), /token/)
  assert.throws(() => buildBackendEnv({ baseEnv: {}, dataDir: '/d', token: 'x'.repeat(31) }), /token/)
})

test('an error about the token never contains the token', () => {
  assert.throws(() => buildBackendEnv({ baseEnv: {}, dataDir: '/d', token: 'secret-but-short' }), (e: Error) => !e.message.includes('secret-but-short'))
})

test('in development the backend runs from source with uv, from the backend folder', () => {
  const c = backendCommand({ packaged: false, appRoot: '/repo', resourcesPath: '/res', platform: 'darwin', port: 8123 })
  assert.equal(c.command, 'uv')
  assert.deepEqual(c.args, ['run', 'python', 'run.py', '--port', '8123', '--exit-when-stdin-closes'])
  assert.equal(c.cwd, '/repo/backend')
})

test('packaged, it is the executable inside the app resources (with .exe on Windows)', () => {
  const mac = backendCommand({ packaged: true, appRoot: '/app', resourcesPath: '/Applications/Clank.app/Contents/Resources', platform: 'darwin', port: 9000 })
  assert.equal(mac.command, '/Applications/Clank.app/Contents/Resources/backend/clank-backend')
  assert.deepEqual(mac.args, ['--port', '9000', '--exit-when-stdin-closes'])
  const win = backendCommand({ packaged: true, appRoot: '/app', resourcesPath: 'C:/Clank/resources', platform: 'win32', port: 9000 })
  assert.equal(win.command, 'C:/Clank/resources/backend/clank-backend.exe')
})

test('the port must be a real one', () => {
  for (const port of [0, -1, 65536, 1.5, NaN]) {
    assert.throws(() => backendCommand({ packaged: false, appRoot: '/r', resourcesPath: '/x', platform: 'darwin', port }), /port/)
  }
})

test('the backend is never given a host: it listens on 127.0.0.1 by itself', () => {
  const c = backendCommand({ packaged: false, appRoot: '/r', resourcesPath: '/x', platform: 'darwin', port: 8000 })
  assert.equal(c.args.some((a: string) => /host|0\.0\.0\.0/.test(a)), false)
})
