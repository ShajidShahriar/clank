// The installer and the code that starts the backend must agree on where the backend is. Nothing here builds anything: it reads the real configuration files
// and the real Python build script and checks that the pieces fit. What is promised:
// - the frozen backend folder the build script makes (`build/backend/clank-backend/`) is copied by the installer to `<resources>/backend/`, OUTSIDE the asar
//   archive (a program cannot be started from inside an archive);
// - the program the app starts, `<resources>/backend/clank-backend`, is the program the build script names;
// - `npm run build` freezes the backend BEFORE it packs the app, so an installer can never be made without one.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { backendCommand } from './env.ts'

const root = new URL('../../', import.meta.url)
const read = (file: string) => readFileSync(new URL(file, root), 'utf8')
const builder = JSON.parse(read('electron-builder.json5').split('\n').filter((line) => !line.trim().startsWith('//')).join('\n'))
const scripts = JSON.parse(read('package.json')).scripts as Record<string, string>
const python = read('backend/freeze.py')
const constant = (name: string) => new RegExp(`^${name} = "([^"]+)"`, 'm').exec(python)?.[1]

test('the installer copies the frozen backend folder to resources/backend', () => {
  const entry = (builder.extraResources ?? []).find((e: { to?: string }) => e.to === 'backend')
  assert.ok(entry, 'no extraResources entry for the backend')
  assert.equal(entry.from, `build/backend/${constant('PROGRAM_NAME')}`)
})

test('the frozen backend is not packed into the asar archive', () => {
  assert.notEqual(builder.asar, undefined)
  for (const pattern of builder.files ?? []) assert.ok(!String(pattern).includes('build/backend') && !String(pattern).includes('clank-backend'), pattern)
})

test('the program the app starts is the program the build script makes', () => {
  const name = constant('PROGRAM_NAME')
  assert.ok(name)
  const resources = path.join(path.sep, 'Applications', 'Clank.app', 'Contents', 'Resources')
  const { command, args } = backendCommand({ packaged: true, appRoot: '/x', resourcesPath: resources, platform: 'darwin', port: 4000 })
  assert.equal(command, `${resources}/backend/${name}`)
  assert.ok(args.includes('--exit-when-stdin-closes'))
  assert.equal(backendCommand({ packaged: true, appRoot: '/x', resourcesPath: 'C:\\Clank\\resources', platform: 'win32', port: 4000 }).command, `C:/Clank/resources/backend/${name}.exe`)
})

test('the build script freezes the backend with the project script, and the full build does it first', () => {
  assert.match(scripts['build:backend'] ?? '', /scripts\/freeze_backend\.py/)
  assert.match(scripts['build:backend'] ?? '', /uv run/)
  const steps = (scripts.build ?? '').split('&&').map((s) => s.trim())
  const backend = steps.indexOf('npm run build:backend')
  const packer = steps.findIndex((s) => s.startsWith('electron-builder'))
  assert.ok(backend >= 0 && packer >= 0 && backend < packer, scripts.build)
})

test('the build script writes where the installer reads', () => {
  assert.match(python, /DEFAULT_OUT = HERE\.parent \/ "build" \/ "backend"/)
})
