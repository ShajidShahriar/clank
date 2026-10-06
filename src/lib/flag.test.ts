// A true/false setting kept in the browser's storage (here: "may code excerpts be sent to a remote model?"). Storage can be missing, full or blocked
// (a private window, cleared site data), so every read and write is guarded and a failure means "the default", never a crash.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFlag, writeFlag } from './flag.ts'

const memory = (initial: Record<string, string> = {}) => {
  const data = { ...initial }
  return { data, getItem: (k: string) => (k in data ? data[k] : null), setItem: (k: string, v: string) => { data[k] = v } }
}

test('a flag that was never saved is the default', () => {
  assert.equal(readFlag(memory(), 'k', false), false)
  assert.equal(readFlag(memory(), 'k', true), true)
})

test('a saved flag is read back', () => {
  const s = memory()
  writeFlag(s, 'k', true)
  assert.equal(readFlag(s, 'k', false), true)
  writeFlag(s, 'k', false)
  assert.equal(readFlag(s, 'k', true), false)
  assert.equal(s.data.k, 'false')
})

test('anything that is not exactly "true" or "false" is the default', () => {
  for (const junk of ['yes', '1', '', 'TRUE', 'null', '{}']) {
    assert.equal(readFlag(memory({ k: junk }), 'k', false), false, junk)
    assert.equal(readFlag(memory({ k: junk }), 'k', true), true, junk)
  }
})

test('storage that is missing or throws gives the default and never throws', () => {
  const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('full') } }
  assert.equal(readFlag(broken, 'k', true), true)
  assert.equal(readFlag(undefined, 'k', false), false)
  assert.doesNotThrow(() => writeFlag(broken, 'k', true))
  assert.doesNotThrow(() => writeFlag(undefined, 'k', true))
})

test('the privacy flag defaults to off: nothing is sent anywhere until the person says so', () => {
  assert.equal(readFlag(memory(), 'clank.allowRemote', false), false)
})
