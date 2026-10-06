// The settings form's logic, apart from the screen: what the fields start as, what happens when the provider changes, what is wrong with what was typed,
// what is sent when "Save" is pressed, and what the person is told about where their code goes.
import test from 'node:test'
import assert from 'node:assert/strict'
import { formFromSettings, hostIsLocal, isDirty, payloadFromForm, privacyNote, switchPreset, validateForm } from './settingsForm.ts'
import type { LlmPreset, LlmSettings } from '../api/types.ts'

const groq: LlmPreset = { id: 'groq', label: 'Groq (hosted)', base_url: 'https://api.groq.com/openai/v1', model: 'openai/gpt-oss-120b', takes_key: true, key_optional: false, local: false,
  base_url_editable: false, context_tokens: 2500, max_output_tokens: 1200, note: 'Hosted.' }
const ollama: LlmPreset = { id: 'ollama', label: 'Ollama', base_url: 'http://localhost:11434/v1', model: 'llama3.2', takes_key: false, key_optional: false, local: true,
  base_url_editable: true, context_tokens: 3000, max_output_tokens: 800, note: 'Local.' }
const custom: LlmPreset = { id: 'custom', label: 'Custom', base_url: '', model: '', takes_key: true, key_optional: true, local: false, base_url_editable: true,
  context_tokens: 2500, max_output_tokens: 1000, note: 'Any.' }
const settings = (active: Partial<LlmSettings['active']> = {}): LlmSettings => ({
  active: { preset: 'groq', label: groq.label, base_url: groq.base_url, model: groq.model, context_tokens: 2500, max_output_tokens: 1200, local: false, takes_key: true, key_optional: false,
    key_set: true, key_source: 'memory', ...active },
  presets: [groq, ollama, custom],
})

test('the form starts as the active choice, with an empty key field and numbers as text', () => {
  assert.deepEqual(formFromSettings(settings()), { preset: 'groq', base_url: groq.base_url, model: groq.model, context_tokens: '2500', max_output_tokens: '1200', apiKey: '', removeKey: false })
})

test('changing the provider fills the new provider\'s own defaults, and keeps nothing of the old one', () => {
  const start = { ...formFromSettings(settings()), apiKey: 'typed-key-1', model: 'edited', removeKey: true }
  assert.deepEqual(switchPreset(start, settings(), 'ollama'), { preset: 'ollama', base_url: ollama.base_url, model: 'llama3.2', context_tokens: '3000', max_output_tokens: '800', apiKey: '', removeKey: false })
  assert.deepEqual(switchPreset(start, settings(), 'custom'), { preset: 'custom', base_url: '', model: '', context_tokens: '2500', max_output_tokens: '1000', apiKey: '', removeKey: false })
})

test('going back to the active provider restores what is saved for it, not the preset defaults', () => {
  const saved = settings({ preset: 'ollama', base_url: 'http://localhost:9999/v1', model: 'qwen2.5-coder:7b', context_tokens: 3500, max_output_tokens: 700, local: true, takes_key: false })
  const away = switchPreset(formFromSettings(saved), saved, 'groq')
  const back = switchPreset(away, saved, 'ollama')
  assert.deepEqual(back, { preset: 'ollama', base_url: 'http://localhost:9999/v1', model: 'qwen2.5-coder:7b', context_tokens: '3500', max_output_tokens: '700', apiKey: '', removeKey: false })
})

test('an unknown provider leaves the form as it is', () => {
  const form = formFromSettings(settings())
  assert.deepEqual(switchPreset(form, settings(), 'nope'), form)
})

// ---- what is wrong

const ok = (over = {}) => ({ ...formFromSettings(settings()), ...over })

test('a good form has no problems', () => {
  assert.deepEqual(validateForm(ok(), groq), {})
  assert.deepEqual(validateForm({ ...switchPreset(ok(), settings(), 'ollama') }, ollama), {})
})

test('the model name is needed and has a limit', () => {
  assert.match(validateForm(ok({ model: '' }), groq).model ?? '', /model/i)
  assert.match(validateForm(ok({ model: '   ' }), groq).model ?? '', /model/i)
  assert.match(validateForm(ok({ model: 'x'.repeat(201) }), groq).model ?? '', /200/)
})

test('the numbers must be whole and in range (500 to 16000 for code, 1 to 20000 for the answer)', () => {
  for (const bad of ['', 'abc', '1.5', '499', '16001', '-1', '2e3', ' ', '0x10']) assert.ok(validateForm(ok({ context_tokens: bad }), groq).context_tokens, `code ${bad}`)
  for (const good of ['500', '16000', ' 2500 ']) assert.equal(validateForm(ok({ context_tokens: good }), groq).context_tokens, undefined, `code ${good}`)
  for (const bad of ['', '0', '20001', '1.5', 'x']) assert.ok(validateForm(ok({ max_output_tokens: bad }), groq).max_output_tokens, `answer ${bad}`)
  for (const good of ['1', '20000']) assert.equal(validateForm(ok({ max_output_tokens: good }), groq).max_output_tokens, undefined, `answer ${good}`)
})

test('an address is checked only where it can be edited', () => {
  assert.equal(validateForm(ok({ base_url: 'whatever' }), groq).base_url, undefined, 'Groq\'s address is fixed and not sent')
  for (const bad of ['', '  ', 'localhost:11434', 'ftp://x/v1', 'not a url', 'http://', 'x'.repeat(301)]) assert.ok(validateForm({ ...switchPreset(ok(), settings(), 'ollama'), base_url: bad }, ollama).base_url, bad)
  for (const good of ['http://localhost:11434/v1', 'https://api.example.com/v1/', 'http://127.0.0.1:1234/v1']) assert.equal(validateForm({ ...switchPreset(ok(), settings(), 'ollama'), base_url: good }, ollama).base_url, undefined, good)
})

test('an empty address says an address is needed (not just that it is malformed)', () => {
  assert.match(validateForm({ ...switchPreset(ok(), settings(), 'ollama'), base_url: '' }, ollama).base_url ?? '', /needed/)
  assert.match(validateForm({ ...switchPreset(ok(), settings(), 'ollama'), base_url: '   ' }, ollama).base_url ?? '', /needed/)
})

test('a service that can take a key must use https unless it runs on this computer', () => {
  const form = { ...switchPreset(ok(), settings(), 'custom'), model: 'm' }
  assert.match(validateForm({ ...form, base_url: 'http://api.example.com/v1' }, custom).base_url ?? '', /https/)
  assert.equal(validateForm({ ...form, base_url: 'https://api.example.com/v1' }, custom).base_url, undefined)
  assert.equal(validateForm({ ...form, base_url: 'http://localhost:8000/v1' }, custom).base_url, undefined)
  assert.equal(validateForm({ ...switchPreset(ok(), settings(), 'ollama'), base_url: 'http://192.168.0.5:11434/v1' }, ollama).base_url, undefined, 'a provider that takes no key may use http')
})

test('a typed key must be visible ASCII with no spaces, up to 512; an empty field is fine', () => {
  assert.equal(validateForm(ok({ apiKey: '' }), groq).apiKey, undefined)
  assert.equal(validateForm(ok({ apiKey: 'gsk_abc123' }), groq).apiKey, undefined)
  for (const bad of ['has space', 'line1\nline2', 'kéy', 'a'.repeat(513)]) assert.ok(validateForm(ok({ apiKey: bad }), groq).apiKey, JSON.stringify(bad))
  assert.equal(validateForm(ok({ apiKey: '  gsk_abc123  ' }), groq).apiKey, undefined, 'surrounding spaces are trimmed, as the backend does')
})

test('a message about a field never repeats what was typed', () => {
  const problems = validateForm({ ...ok(), apiKey: 'has space SECRET', model: '', context_tokens: 'CODEBUDGET', max_output_tokens: 'ANSWERLEN' }, groq)
  for (const message of Object.values(problems)) for (const typed of ['SECRET', 'CODEBUDGET', 'ANSWERLEN']) assert.equal(String(message).includes(typed), false)
})

// ---- what is sent

test('the payload has the choice, numbers as numbers, and the address only where it can be edited', () => {
  assert.deepEqual(payloadFromForm(ok(), groq), { config: { preset: 'groq', model: 'openai/gpt-oss-120b', context_tokens: 2500, max_output_tokens: 1200 }, apiKey: undefined })
  const local = { ...switchPreset(ok(), settings(), 'ollama'), model: ' llama3.2:3b ', base_url: ' http://localhost:11434/v1/ ' }
  assert.deepEqual(payloadFromForm(local, ollama).config, { preset: 'ollama', base_url: 'http://localhost:11434/v1', model: 'llama3.2:3b', context_tokens: 3000, max_output_tokens: 800 })
})

test('the key: a typed key is sent trimmed, "remove" sends null, an empty field sends nothing', () => {
  assert.equal(payloadFromForm(ok({ apiKey: '  gsk_abc123 ' }), groq).apiKey, 'gsk_abc123')
  assert.equal(payloadFromForm(ok({ removeKey: true }), groq).apiKey, null)
  assert.equal(payloadFromForm(ok({ apiKey: '' }), groq).apiKey, undefined)
  assert.equal(payloadFromForm(ok({ apiKey: 'typed', removeKey: true }), groq).apiKey, null, 'remove wins: the person pressed it last')
})

test('a provider that takes no key never sends one', () => {
  const form = { ...switchPreset(ok(), settings(), 'ollama'), apiKey: 'typed-key', removeKey: true }
  assert.equal(payloadFromForm(form, ollama).apiKey, undefined)
})

// ---- where the code goes

test('the person is told where their code goes', () => {
  assert.match(privacyNote(ok(), groq), /api\.groq\.com/)
  assert.match(privacyNote(ok(), groq), /Send code to remote model/)
  assert.match(privacyNote({ ...switchPreset(ok(), settings(), 'ollama') }, ollama), /this computer/i)
  assert.match(privacyNote({ ...switchPreset(ok(), settings(), 'custom'), base_url: 'https://openrouter.ai/api/v1' }, custom), /openrouter\.ai/)
  assert.match(privacyNote({ ...switchPreset(ok(), settings(), 'ollama'), base_url: 'http://192.168.0.5:11434/v1' }, ollama), /192\.168\.0\.5/, 'a local preset pointed at another machine is remote')
  assert.match(privacyNote({ ...switchPreset(ok(), settings(), 'custom'), base_url: '' }, custom), /service/i)
})

test('hostIsLocal means this computer and nothing that only looks like it', () => {
  for (const url of ['http://localhost:11434/v1', 'http://127.0.0.1:1234/v1', 'http://127.5.5.5/v1', 'http://[::1]:8080/v1']) assert.equal(hostIsLocal(url), true, url)
  for (const url of ['https://api.groq.com/v1', 'http://localhost.evil.com/v1', 'http://127.0.0.1.evil.com/v1', 'http://192.168.0.5/v1', 'http://0.0.0.0/v1', '', 'junk']) assert.equal(hostIsLocal(url), false, url)
})

test('the form is dirty when anything differs from what is saved, or a key was typed or removed', () => {
  const saved = settings()
  assert.equal(isDirty(formFromSettings(saved), saved), false)
  assert.equal(isDirty(ok({ model: 'other' }), saved), true)
  assert.equal(isDirty(ok({ context_tokens: '2600' }), saved), true)
  assert.equal(isDirty(ok({ apiKey: 'typed' }), saved), true)
  assert.equal(isDirty(ok({ removeKey: true }), saved), true)
  assert.equal(isDirty(switchPreset(ok(), saved, 'ollama'), saved), true)
  assert.equal(isDirty(ok({ model: ' openai/gpt-oss-120b ' }), saved), false, 'spaces only')
  assert.equal(isDirty({ ...formFromSettings(saved), preset: 'ollama' }, saved), true, 'a different provider is a change even when every field is the same')
})
