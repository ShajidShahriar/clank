// Task: saving the answer-model settings from the window. One operation does it in a safe order: (1) the backend checks and stores the CHOICE; only if that works
// (2) the key is stored or removed in the encrypted key store; (3) the key of the ACTIVE provider (or nothing) is pushed to the backend's memory; (4) the new
// settings go back to the window. A key is never part of any answer, and a key saved for one provider is never pushed while another is active.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createSettingsFlow } from './settings-flow.ts'
import { KeyStoreError } from './secrets.ts'

const KEY = 'gsk_' + 'k3y'.repeat(12)
type Call = { method: string, path: string, body?: unknown }

function setup(options: { replies?: Record<string, { ok: boolean, status: number, body: unknown }>, stored?: Record<string, string>, failSave?: KeyStoreError } = {}) {
  const calls: Call[] = []
  const stored: Record<string, string> = { ...(options.stored ?? {}) }
  const log: string[] = []
  const replies = {
    'PUT /settings/llm': { ok: true, status: 200, body: { active: { preset: 'groq' } } },
    'PUT /settings/llm/key': { ok: true, status: 200, body: { key_set: true } },
    'GET /settings/llm': { ok: true, status: 200, body: { active: { preset: 'groq', key_set: true } } },
    ...(options.replies ?? {}),
  } as Record<string, { ok: boolean, status: number, body: unknown }>
  const service = {
    request: async (method: string, path: string, body?: unknown) => {
      calls.push({ method, path, body })
      log.push(`${method} ${path}`)
      return replies[`${method} ${path}`]
    },
  }
  const keys = {
    available: () => true,
    save: (preset: string, key: string) => { log.push(`save ${preset}`); if (options.failSave) throw options.failSave; stored[preset] = key },
    load: (preset: string) => stored[preset] ?? null,
    has: (preset: string) => preset in stored,
    remove: (preset: string) => { log.push(`remove ${preset}`); delete stored[preset] },
  }
  return { flow: createSettingsFlow({ service: service as never, keys }), calls, stored, log }
}

test('saving goes: choice, key, push, read back, and returns the new settings', async () => {
  const { flow, calls, stored, log } = setup()
  const reply = await flow.save({ config: { preset: 'groq', model: 'openai/gpt-oss-120b' }, apiKey: KEY })
  assert.deepEqual(log, ['PUT /settings/llm', 'save groq', 'PUT /settings/llm/key', 'GET /settings/llm'])
  assert.deepEqual(calls[0].body, { preset: 'groq', model: 'openai/gpt-oss-120b' })
  assert.deepEqual(calls[1].body, { api_key: KEY }, 'the push is the second call to the backend (the key store save is not one)')
  assert.equal(stored.groq, KEY)
  assert.deepEqual(reply, { ok: true, status: 200, body: { active: { preset: 'groq', key_set: true } } })
})

test('only the known fields of the choice are sent on', async () => {
  const { flow, calls } = setup()
  await flow.save({ config: { preset: 'custom', base_url: 'https://x/v1', model: 'm', context_tokens: 2500, max_output_tokens: 900, api_key: 'sneaky', other: 1 } as never })
  assert.deepEqual(calls[0].body, { preset: 'custom', base_url: 'https://x/v1', model: 'm', context_tokens: 2500, max_output_tokens: 900 })
})

test('with no key given the stored key of that provider is left alone and pushed', async () => {
  const { flow, calls, log } = setup({ stored: { groq: KEY } })
  await flow.save({ config: { preset: 'groq', model: 'x' } })
  assert.deepEqual(log, ['PUT /settings/llm', 'PUT /settings/llm/key', 'GET /settings/llm'])
  assert.deepEqual(calls[1].body, { api_key: KEY })
})

test('a null key removes the stored one and clears the backend\'s', async () => {
  const { flow, calls, stored, log } = setup({ stored: { groq: KEY } })
  await flow.save({ config: { preset: 'groq' }, apiKey: null })
  assert.deepEqual(log, ['PUT /settings/llm', 'remove groq', 'PUT /settings/llm/key', 'GET /settings/llm'])
  assert.deepEqual(calls[1].body, { api_key: null })
  assert.equal('groq' in stored, false)
})

test('switching provider pushes the NEW provider\'s key, or nothing: never the old one', async () => {
  const { flow, calls } = setup({ stored: { groq: KEY } })
  await flow.save({ config: { preset: 'ollama' } })
  assert.deepEqual(calls[1].body, { api_key: null }, 'ollama has no stored key: the backend memory is cleared, groq\'s key is not sent to a local server')
  const second = setup({ stored: { groq: KEY, custom: 'custom-key-1' } })
  await second.flow.save({ config: { preset: 'custom', base_url: 'https://x/v1', model: 'm' } })
  assert.deepEqual(second.calls[1].body, { api_key: 'custom-key-1' })
})

test('a choice the backend refuses touches no key and sends nothing more', async () => {
  const refused = { ok: false, status: 422, body: { error: { code: 'invalid_settings', message: 'The code budget must be a whole number from 500 to 16000.' } } }
  const { flow, calls, stored, log } = setup({ replies: { 'PUT /settings/llm': refused } })
  assert.deepEqual(await flow.save({ config: { preset: 'groq', context_tokens: 5 }, apiKey: KEY }), refused)
  assert.deepEqual(log, ['PUT /settings/llm'])
  assert.equal(calls.length, 1)
  assert.deepEqual(stored, {})
})

test('when secure storage is not available the choice stays saved, the key is not stored or pushed, and the window is told', async () => {
  const { flow, log } = setup({ failSave: new KeyStoreError('unavailable', 'Secure storage is not available on this computer, so the key was not saved.') })
  const reply = await flow.save({ config: { preset: 'groq' }, apiKey: KEY })
  assert.deepEqual(log, ['PUT /settings/llm', 'save groq'])
  assert.equal(reply.ok, false)
  assert.deepEqual((reply.body as { error: { code: string } }).error.code, 'secure_storage_unavailable')
  assert.equal(JSON.stringify(reply).includes(KEY), false)
})

test('other key store failures have their own codes', async () => {
  for (const [code, expected] of [['invalid_key', 'invalid_key'], ['write_failed', 'key_save_failed'], ['invalid_preset', 'invalid_key']] as const) {
    const { flow } = setup({ failSave: new KeyStoreError(code, 'a message') })
    const reply = await flow.save({ config: { preset: 'groq' }, apiKey: KEY })
    assert.equal((reply.body as { error: { code: string } }).error.code, expected, code)
  }
})

test('a failure to push the key is reported', async () => {
  const failing = { ok: false, status: 422, body: { error: { code: 'invalid_key', message: 'A key has only visible ASCII characters.' } } }
  const { flow } = setup({ replies: { 'PUT /settings/llm/key': failing } })
  assert.deepEqual(await flow.save({ config: { preset: 'groq' }, apiKey: KEY }), failing)
})

test('the key is never in what goes back to the window', async () => {
  const { flow } = setup()
  assert.equal(JSON.stringify(await flow.save({ config: { preset: 'groq' }, apiKey: KEY })).includes(KEY), false)
})

test('a malformed request is refused and nothing is called', async () => {
  const { flow, calls, log } = setup()
  for (const payload of [undefined, null, 'x', 5, [], {}, { config: null }, { config: 'groq' }, { config: [] }, { config: {} }, { config: { preset: 5 } },
    { config: { preset: 'groq', context_tokens: '2500' } }, { config: { preset: 'groq', model: 5 } }, { config: { preset: 'groq', base_url: {} } },
    { config: { preset: 'groq' }, apiKey: 5 }, { config: { preset: 'groq' }, apiKey: {} }, { config: { preset: 'groq' }, apiKey: true }]) {
    const reply = await flow.save(payload as never)
    assert.equal(reply.ok, false, JSON.stringify(payload))
    assert.equal((reply.body as { error: { code: string } }).error.code, 'bad_request', JSON.stringify(payload))
  }
  assert.equal(calls.length, 0)
  assert.deepEqual(log, [])
})

test('after the backend starts, the key of the active provider is pushed', async () => {
  const { flow, calls } = setup({ stored: { groq: KEY } })
  await flow.onBackendReady()
  assert.deepEqual(calls.map((c) => `${c.method} ${c.path}`), ['GET /settings/llm', 'PUT /settings/llm/key'])
  assert.deepEqual(calls[1].body, { api_key: KEY })
})

test('after the backend starts with no stored key, the backend\'s memory is cleared, not left alone', async () => {
  const { flow, calls } = setup({ stored: { custom: 'k-123456' } })
  await flow.onBackendReady()
  assert.deepEqual(calls[1].body, { api_key: null })
})

test('a backend that is not ready or sends nonsense never makes onBackendReady throw or push', async () => {
  for (const reply of [{ ok: false, status: 0, body: null }, { ok: true, status: 200, body: null }, { ok: true, status: 200, body: { active: {} } }, { ok: true, status: 200, body: { active: { preset: 5 } } }]) {
    const { flow, calls } = setup({ stored: { groq: KEY }, replies: { 'GET /settings/llm': reply } })
    await assert.doesNotReject(flow.onBackendReady())
    assert.equal(calls.length, 1, JSON.stringify(reply))
  }
  const throwing = createSettingsFlow({ service: { request: async () => { throw new Error('boom') } } as never, keys: { available: () => true, save() {}, load: () => KEY, has: () => true, remove() {} } })
  await assert.doesNotReject(throwing.onBackendReady())
  assert.deepEqual(await throwing.save({ config: { preset: 'groq' } }).then((r) => [r.ok, r.status]), [false, 0], 'a throwing service is an error reply, not an exception')
})
