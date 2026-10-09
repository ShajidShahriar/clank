// In development the Dock and the menu bar show the name of the Electron program that runs the app ("Electron") and its icon. `npm run dev` therefore first gives that
// copy of Electron (inside node_modules) the app's name and icon (scripts/brand-dev-electron.mjs). A packaged app needs none of this: electron-builder names it.
// What is promised: only what differs is changed; a copy that already has the name and the icon is left alone and NOT signed again; after a change the bundle is signed
// again (a changed bundle with its old signature would not start on Apple Silicon); a running copy is never touched; and a failure never stops `npm run dev`.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { NAME, brand, needed } from '../../scripts/brand-dev-electron.mjs'

type Probe = { name: string, displayName: string, sameIcon: boolean }

function bundle(probe: Probe, { running = false, failAt = '' } = {}) {
  const log: string[] = []
  const state = { ...probe }
  const ops = {
    running: () => running,
    read: (key: string) => (key === 'CFBundleName' ? state.name : state.displayName),
    set: (key: string, value: string) => { if (failAt === 'set') throw new Error('plist'); log.push(`set ${key} ${value}`); if (key === 'CFBundleName') state.name = value; else state.displayName = value },
    sameIcon: () => state.sameIcon,
    copyIcon: () => { if (failAt === 'icon') throw new Error('icon'); log.push('icon'); state.sameIcon = true },
    sign: () => { if (failAt === 'sign') throw new Error('sign'); log.push('sign') },
  }
  return { ops, log, state }
}
const plain: Probe = { name: 'Electron', displayName: 'Electron', sameIcon: false }

test('the name is Clank', () => {
  assert.equal(NAME, 'Clank')
})

test('what has to change is what differs', () => {
  assert.deepEqual(needed(plain, NAME), { name: true, displayName: true, icon: true })
  assert.deepEqual(needed({ name: 'Clank', displayName: 'Electron', sameIcon: true }, NAME), { name: false, displayName: true, icon: false })
  assert.deepEqual(needed({ name: 'Clank', displayName: 'Clank', sameIcon: true }, NAME), { name: false, displayName: false, icon: false })
  assert.deepEqual(needed({ name: 'Electron', displayName: 'Clank', sameIcon: false }, NAME), { name: true, displayName: false, icon: true })
})

test('a plain Electron gets the name, the icon and a new signature, in that order', () => {
  const { ops, log } = bundle(plain)
  assert.deepEqual(brand(ops), { changed: true, skipped: null })
  assert.deepEqual(log, ['set CFBundleName Clank', 'set CFBundleDisplayName Clank', 'icon', 'sign'])
})

test('only the differing parts are written', () => {
  const { ops, log } = bundle({ name: 'Clank', displayName: 'Electron', sameIcon: true })
  brand(ops)
  assert.deepEqual(log, ['set CFBundleDisplayName Clank', 'sign'])
})

test('a copy whose display name is right but whose name is not gets only the name', () => {
  const { ops, log } = bundle({ name: 'Electron', displayName: 'Clank', sameIcon: true })
  brand(ops)
  assert.deepEqual(log, ['set CFBundleName Clank', 'sign'])
})

test('a copy that is already branded is left alone and not signed again', () => {
  const { ops, log } = bundle({ name: 'Clank', displayName: 'Clank', sameIcon: true })
  assert.deepEqual(brand(ops), { changed: false, skipped: null })
  assert.deepEqual(log, [])
})

test('branding twice changes nothing the second time', () => {
  const { ops, log } = bundle(plain)
  brand(ops)
  log.length = 0
  assert.deepEqual(brand(ops), { changed: false, skipped: null })
  assert.deepEqual(log, [])
})

test('a copy of Electron that is running is not touched, and that is said', () => {
  const { ops, log } = bundle(plain, { running: true })
  const result = brand(ops)
  assert.equal(result.changed, false)
  assert.match(String(result.skipped), /running/i)
  assert.deepEqual(log, [])
})

test('a running copy that needs nothing is not a problem', () => {
  const { ops } = bundle({ name: 'Clank', displayName: 'Clank', sameIcon: true }, { running: true })
  assert.deepEqual(brand(ops), { changed: false, skipped: null })
})

test('a failing step becomes a message and never an exception (npm run dev must still start)', () => {
  for (const failAt of ['set', 'icon', 'sign']) {
    const { ops } = bundle(plain, { failAt })
    const result = brand(ops)
    assert.equal(result.changed, false, failAt)
    assert.ok(typeof result.skipped === 'string' && result.skipped.length > 0, failAt)
  }
})

test('the dev script runs before `npm run dev`, and only the project script does it', () => {
  const scripts = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8')).scripts as Record<string, string>
  assert.equal(scripts.predev, 'node scripts/brand-dev-electron.mjs')
})
