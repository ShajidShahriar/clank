// Where the answer-model key lives on this computer (settings step). Encrypted with the system keychain (Electron safeStorage), one key per provider, in a
// file only this user can read. NEVER in plain text: if the system cannot encrypt, saving is refused and nothing is written. The window can write a key but never
// read one back; only the main process loads it, to push it to the backend's memory. Erasable TypeScript only, so that `node --test` can run it.
import fs from 'node:fs'
import path from 'node:path'

const FILE = 'llm-keys.json'
const PRESET = /^[a-z0-9_-]{1,40}$/
const RESERVED = new Set(['__proto__', 'constructor', 'prototype'])
const KEY = /^[\x21-\x7e]{1,512}$/                 // visible ASCII, no spaces: what the backend accepts too

export class KeyStoreError extends Error {
  code: 'unavailable' | 'invalid_preset' | 'invalid_key' | 'write_failed'

  constructor(code: KeyStoreError['code'], message: string) {
    super(message)
    this.code = code
  }
}

export type SafeStorageLike = {
  isEncryptionAvailable(): boolean
  encryptString(text: string): Buffer
  decryptString(data: Buffer): string
}

export type KeyStore = {
  available(): boolean
  save(preset: string, key: string): void
  load(preset: string): string | null
  has(preset: string): boolean
  remove(preset: string): void
}

const validPreset = (preset: unknown): preset is string => typeof preset === 'string' && PRESET.test(preset) && !RESERVED.has(preset)
const own = (object: Record<string, unknown>, name: string) => Object.prototype.hasOwnProperty.call(object, name)

export function createKeyStore({ dir, safeStorage }: { dir: string, safeStorage: SafeStorageLike }): KeyStore {
  const file = path.join(dir, FILE)

  const available = (): boolean => {
    try {
      return safeStorage.isEncryptionAvailable()
    } catch {
      return false
    }
  }

  /** The stored map of provider -> encrypted key (base64). A missing, damaged or oddly shaped file is an empty map. */
  const read = (): Record<string, string> => {
    try {
      const parsed: unknown = JSON.parse(fs.readFileSync(file, 'utf8'))
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}
      const keys = (parsed as Record<string, unknown>).keys
      if (typeof keys !== 'object' || keys === null || Array.isArray(keys)) return {}
      const clean: Record<string, string> = {}
      for (const [name, value] of Object.entries(keys)) if (validPreset(name) && typeof value === 'string') clean[name] = value
      return clean
    } catch {
      return {}
    }
  }

  const write = (keys: Record<string, string>): void => {
    const temporary = `${file}.tmp-${process.pid}`
    try {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true, mode: 0o700 })
      fs.writeFileSync(temporary, JSON.stringify({ version: 1, keys }), { mode: 0o600 })
      fs.renameSync(temporary, file)
    } catch {
      try { fs.rmSync(temporary, { force: true }) } catch { /* nothing to clean */ }
      throw new KeyStoreError('write_failed', 'The key could not be saved on this computer.')
    }
  }

  return {
    available,

    save(preset, key) {
      if (!validPreset(preset)) throw new KeyStoreError('invalid_preset', 'That provider name is not valid.')
      if (typeof key !== 'string' || !KEY.test(key)) throw new KeyStoreError('invalid_key', 'A key has only visible ASCII characters, no spaces or line breaks, and at most 512 of them.')
      if (!available()) throw new KeyStoreError('unavailable', 'Secure storage is not available on this computer, so the key was not saved.')
      const keys = read()
      keys[preset] = safeStorage.encryptString(key).toString('base64')
      write(keys)
    },

    load(preset) {
      if (!validPreset(preset) || !available()) return null
      const keys = read()
      if (!own(keys, preset)) return null
      try {
        const key = safeStorage.decryptString(Buffer.from(keys[preset], 'base64'))
        return KEY.test(key) ? key : null
      } catch {
        return null
      }
    },

    has(preset) {
      return this.load(preset) !== null
    },

    remove(preset) {
      if (!validPreset(preset)) return
      const keys = read()
      if (!own(keys, preset)) return
      delete keys[preset]
      write(keys)
    },
  }
}
