// Task: where the answer-model key lives on this computer. Encrypted with the system keychain (Electron safeStorage), one key per provider, in a file only this
// user can read. It is NEVER written in plain text: if the system cannot encrypt, saving is refused and nothing is written. The window can write a key but
// never read one back; only the main process loads it, to push it to the backend's memory.
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { createKeyStore, KeyStoreError } from './secrets.ts'

const KEY = 'gsk_' + 'k3y'.repeat(12)

function fakeSafeStorage(available = true) {
  return {
    available,
    isEncryptionAvailable() { return this.available },
    encryptString(text: string) { return Buffer.from('enc:' + [...text].reverse().join('')) },
    decryptString(data: Buffer) {
      const raw = data.toString()
      if (!raw.startsWith('enc:')) throw new Error('cannot decrypt')
      return [...raw.slice(4)].reverse().join('')
    },
  }
}

function setup(available = true) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'clank-keys-'))
  const safe = fakeSafeStorage(available)
  const store = createKeyStore({ dir, safeStorage: safe })
  return { dir, safe, store, file: path.join(dir, 'llm-keys.json') }
}

test('a saved key is read back for its provider and for no other', () => {
  const { store } = setup()
  store.save('groq', KEY)
  assert.equal(store.load('groq'), KEY)
  assert.equal(store.load('custom'), null)
  assert.equal(store.has('groq'), true)
  assert.equal(store.has('custom'), false)
})

test('keys of different providers are kept apart and one can be removed', () => {
  const { store } = setup()
  store.save('groq', KEY)
  store.save('custom', 'other-key-123')
  store.remove('groq')
  assert.equal(store.load('groq'), null)
  assert.equal(store.load('custom'), 'other-key-123')
  store.remove('groq')                                          // removing what is not there is fine
})

test('saving again replaces the key', () => {
  const { store } = setup()
  store.save('groq', KEY)
  store.save('groq', 'second-key-456')
  assert.equal(store.load('groq'), 'second-key-456')
})

test('the key is never in the file in plain text', () => {
  const { store, file } = setup()
  store.save('groq', KEY)
  const content = fs.readFileSync(file, 'utf8')
  assert.equal(content.includes(KEY), false)
  assert.equal(content.includes(KEY.slice(4, 20)), false)
  assert.equal(content.includes([...KEY].reverse().join('')), false, 'not even the reversed text of the fake cipher: it is base64')
  assert.ok(JSON.parse(content).keys.groq, 'but the provider is there')
})

test('the file and its folder are private to this user', () => {
  const { store, file, dir } = setup()
  store.save('groq', KEY)
  assert.equal(fs.statSync(file).mode & 0o777, 0o600)
  assert.equal(fs.statSync(dir).mode & 0o077, 0, 'no group or other access to the folder')
})

test('when the system cannot encrypt, saving is refused and nothing is written', () => {
  const { store, dir, file } = setup(false)
  assert.equal(store.available(), false)
  assert.throws(() => store.save('groq', KEY), (e: unknown) => e instanceof KeyStoreError && e.code === 'unavailable')
  assert.equal(fs.existsSync(file), false)
  assert.deepEqual(fs.readdirSync(dir), [])
})

test('a key that was saved cannot be read when the system can no longer decrypt (null, not a crash)', () => {
  const { store, safe } = setup()
  store.save('groq', KEY)
  safe.available = false
  assert.equal(store.load('groq'), null)
})

test('a file that is damaged is treated as empty, and the next save repairs it', () => {
  const { store, file } = setup()
  fs.writeFileSync(file, '{not json')
  assert.equal(store.load('groq'), null)
  assert.equal(store.has('groq'), false)
  store.save('groq', KEY)
  assert.equal(store.load('groq'), KEY)
})

test('data in the file that does not decrypt is null', () => {
  const { store, file } = setup()
  fs.writeFileSync(file, JSON.stringify({ version: 1, keys: { groq: Buffer.from('garbage').toString('base64') } }))
  assert.equal(store.load('groq'), null)
})

test('a file with the wrong shape is treated as empty', () => {
  const { store, file } = setup()
  for (const junk of ['[]', 'null', '5', '{"keys": 5}', '{"keys": []}', '{"version": 1}']) {
    fs.writeFileSync(file, junk)
    assert.equal(store.load('groq'), null, junk)
  }
})

test('only sensible provider names and keys are accepted', () => {
  const { store } = setup()
  for (const preset of ['', 'GROQ', 'has space', '../x', 'a'.repeat(41), '__proto__x/..']) {
    assert.throws(() => store.save(preset, KEY), (e: unknown) => e instanceof KeyStoreError && e.code === 'invalid_preset', JSON.stringify(preset))
    assert.equal(store.load(preset), null)
  }
  for (const key of ['', ' ', 'has space', 'line1\nline2', 'kéy', 'a'.repeat(513), 5 as never]) {
    assert.throws(() => store.save('groq', key), (e: unknown) => e instanceof KeyStoreError && e.code === 'invalid_key', JSON.stringify(key))
  }
  assert.equal(store.has('groq'), false)
})

test('an error never contains the key', () => {
  const { store } = setup(false)
  try { store.save('groq', KEY) } catch (e) { assert.equal(String((e as Error).message).includes(KEY), false) }
  const { store: s2 } = setup()
  try { s2.save('groq', 'bad key with spaces ' + KEY) } catch (e) { assert.equal(String((e as Error).message).includes(KEY), false) }
})

test('a provider name like __proto__ cannot pollute anything', () => {
  const { store } = setup()
  assert.throws(() => store.save('__proto__', KEY))
  assert.equal(({} as Record<string, unknown>).polluted, undefined)
  assert.equal(store.load('constructor'), null)
})

test('the key survives a new store object on the same folder (a restart)', () => {
  const { store, dir, safe } = setup()
  store.save('groq', KEY)
  assert.equal(createKeyStore({ dir, safeStorage: safe }).load('groq'), KEY)
})

test('no temporary file is left behind after a save', () => {
  const { store, dir } = setup()
  store.save('groq', KEY)
  store.save('custom', 'abc-123-key')
  assert.deepEqual(fs.readdirSync(dir).sort(), ['llm-keys.json'])
})

test('a folder that this store creates is private to this user', () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'clank-keys-parent-'))
  const dir = path.join(parent, 'secrets')
  const store = createKeyStore({ dir, safeStorage: fakeSafeStorage() })
  store.save('groq', KEY)
  assert.equal(fs.statSync(dir).mode & 0o077, 0, 'no group or other access to the folder it made')
})

test('a list where the keys should be is not read as keys (an index like "0" is not a provider)', () => {
  const { store, file } = setup()
  fs.writeFileSync(file, JSON.stringify({ version: 1, keys: [Buffer.from('enc:' + [...KEY].reverse().join('')).toString('base64')] }))
  assert.equal(store.load('0'), null)
  assert.equal(store.has('0'), false)
})

test('something that decrypts but is not a usable key is not returned', () => {
  const { store, file, safe } = setup()
  fs.writeFileSync(file, JSON.stringify({ version: 1, keys: { groq: safe.encryptString('has a space').toString('base64'), custom: safe.encryptString('x'.repeat(600)).toString('base64') } }))
  assert.equal(store.load('groq'), null)
  assert.equal(store.load('custom'), null)
})
