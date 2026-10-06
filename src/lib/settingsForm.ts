// The settings form's logic, apart from the screen (settings step): what the fields start as, what happens when the provider changes, what is wrong with what was
// typed, what is sent on Save, and what the person is told about where their code goes. Pure; erasable TypeScript only, so that `node --test` can run it.
import type { LlmPreset, LlmSettings } from '../api/types.ts'

export type Form = { preset: string, base_url: string, model: string, context_tokens: string, max_output_tokens: string, apiKey: string, removeKey: boolean }
export type FormErrors = { base_url?: string, model?: string, context_tokens?: string, max_output_tokens?: string, apiKey?: string }

const KEY = /^[\x21-\x7e]{1,512}$/

export function formFromSettings(settings: LlmSettings): Form {
  const a = settings.active
  return { preset: a.preset, base_url: a.base_url, model: a.model, context_tokens: String(a.context_tokens), max_output_tokens: String(a.max_output_tokens), apiKey: '', removeKey: false }
}

/** The form after the provider was changed: that provider's own defaults (or, for the one that is saved, what is saved), and nothing of the old one, key included. */
export function switchPreset(form: Form, settings: LlmSettings, id: string): Form {
  if (id === settings.active.preset) return formFromSettings(settings)
  const preset = settings.presets.find((p) => p.id === id)
  if (!preset) return form
  return { preset: id, base_url: preset.base_url, model: preset.model, context_tokens: String(preset.context_tokens), max_output_tokens: String(preset.max_output_tokens), apiKey: '', removeKey: false }
}

/** Is this address on this computer? (localhost, 127.x.x.x, ::1.) Nothing that only looks like it. */
export function hostIsLocal(url: string): boolean {
  let host: string
  try {
    host = new URL(url).hostname.toLowerCase()
  } catch {
    return false
  }
  if (host === 'localhost' || host === '[::1]' || host === '::1') return true
  const parts = /^127\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(host)
  return parts !== null && parts.slice(1).every((n) => Number(n) <= 255)
}

const whole = (text: string) => (/^\s*\d+\s*$/.test(text) ? Number(text) : NaN)

function addressProblem(text: string, preset: LlmPreset): string | undefined {
  const url = text.trim()
  if (url === '') return 'An address is needed, for example https://your-server/v1.'
  if (url.length > 300) return 'The address is longer than 300 characters.'
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return 'The address must start with http:// or https://, for example https://your-server/v1.'
  }
  if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || parsed.hostname === '') return 'The address must start with http:// or https://, for example https://your-server/v1.'
  if (preset.takes_key && parsed.protocol === 'http:' && !hostIsLocal(url)) return 'Use https unless the service runs on this computer: a key is never sent over plain http to another machine.'
  return undefined
}

export function validateForm(form: Form, preset: LlmPreset): FormErrors {
  const errors: FormErrors = {}
  if (preset.base_url_editable) {
    const problem = addressProblem(form.base_url, preset)
    if (problem) errors.base_url = problem
  }
  const model = form.model.trim()
  if (model === '') errors.model = 'A model name is needed.'
  else if (model.length > 200) errors.model = 'The model name is longer than 200 characters.'
  const budget = whole(form.context_tokens)
  if (!(budget >= 500 && budget <= 16000)) errors.context_tokens = 'The code budget must be a whole number from 500 to 16000.'
  const length = whole(form.max_output_tokens)
  if (!(length >= 1 && length <= 20000)) errors.max_output_tokens = 'The answer length must be a whole number from 1 to 20000.'
  const key = form.apiKey.trim()
  if (key !== '' && !KEY.test(key)) errors.apiKey = 'A key has only visible ASCII characters, no spaces or line breaks, and at most 512 of them.'
  return errors
}

/** What Save sends: the choice (numbers as numbers, the address only where it can be edited) and the key (a string stores it, null removes it, undefined leaves it). */
export function payloadFromForm(form: Form, preset: LlmPreset): { config: Record<string, unknown>, apiKey: string | null | undefined } {
  const config: Record<string, unknown> = { preset: form.preset, model: form.model.trim(), context_tokens: whole(form.context_tokens), max_output_tokens: whole(form.max_output_tokens) }
  if (preset.base_url_editable) config.base_url = form.base_url.trim().replace(/\/+$/, '')
  let apiKey: string | null | undefined
  if (preset.takes_key) {
    if (form.removeKey) apiKey = null
    else if (form.apiKey.trim() !== '') apiKey = form.apiKey.trim()
  }
  return { config, apiKey }
}

/** Where the person's code goes, in a sentence. A "local" provider pointed at another machine is remote, because the address decides. */
export function privacyNote(form: Form, preset: LlmPreset): string {
  const url = preset.base_url_editable ? form.base_url.trim() : preset.base_url
  if (hostIsLocal(url)) return 'Runs on this computer. Nothing leaves it.'
  let host = ''
  try {
    host = new URL(url).hostname
  } catch {
    host = ''
  }
  return host ? `Code excerpts are sent to ${host} when "Send code to remote model" is on.` : 'Code excerpts are sent to this service when "Send code to remote model" is on.'
}

export function isDirty(form: Form, saved: LlmSettings): boolean {
  const a = saved.active
  if (form.preset !== a.preset) return true
  if (form.apiKey.trim() !== '' || form.removeKey) return true
  return form.base_url.trim() !== a.base_url || form.model.trim() !== a.model || form.context_tokens.trim() !== String(a.context_tokens) || form.max_output_tokens.trim() !== String(a.max_output_tokens)
}
