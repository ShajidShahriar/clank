// Saving the answer-model settings from the window (settings step). One operation, in a safe order: (1) the backend checks and stores the CHOICE; only if that
// works (2) the key is stored or removed in the encrypted key store; (3) the key of the ACTIVE provider (or nothing) is pushed to the backend's memory;
// (4) the new settings go back to the window. A key is never part of any answer, and a key saved for one provider is never pushed while another is active.
import { KeyStoreError, type KeyStore } from './secrets.ts'

export type Reply = { ok: boolean, status: number, body: unknown }
type ServiceLike = { request(method: string, path: string, body?: unknown): Promise<Reply> }

export type SettingsPayload = {
  config: { preset: string, base_url?: string, model?: string, context_tokens?: number, max_output_tokens?: number }
  apiKey?: string | null                 // a string stores it, null removes it, missing leaves the stored one alone
}

const FIELDS = ['base_url', 'model', 'context_tokens', 'max_output_tokens'] as const

const failure = (status: number, code: string, message: string): Reply => ({ ok: false, status, body: { error: { code, message } } })
const BAD_REQUEST = failure(0, 'bad_request', 'The request was not allowed.')
const UNREACHABLE = failure(0, 'backend_unavailable', 'The Clank backend is not reachable.')

const KEY_ERRORS: Record<KeyStoreError['code'], string> = {
  unavailable: 'secure_storage_unavailable',
  invalid_key: 'invalid_key',
  invalid_preset: 'invalid_key',
  write_failed: 'key_save_failed',
}

/** The choice's known fields only, or null when the payload is not what it must be. */
function check(payload: unknown): { config: Record<string, unknown>, preset: string, apiKey: string | null | undefined } | null {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) return null
  const { config, apiKey } = payload as Record<string, unknown>
  if (typeof config !== 'object' || config === null) return null          // (a list has no `preset`, so the next line refuses it)
  const given = config as Record<string, unknown>
  if (typeof given.preset !== 'string' || given.preset === '') return null
  const clean: Record<string, unknown> = { preset: given.preset }
  for (const field of FIELDS) {
    const value = given[field]
    if (value === undefined || value === null) continue
    if ((field === 'context_tokens' || field === 'max_output_tokens') ? typeof value !== 'number' : typeof value !== 'string') return null
    clean[field] = value
  }
  if (apiKey !== undefined && apiKey !== null && typeof apiKey !== 'string') return null
  return { config: clean, preset: given.preset, apiKey: apiKey as string | null | undefined }
}

export function createSettingsFlow({ service, keys }: { service: ServiceLike, keys: KeyStore }) {
  const pushKey = (preset: string) => service.request('PUT', '/settings/llm/key', { api_key: keys.load(preset) })        // null clears the backend's memory

  return {
    async save(payload: SettingsPayload): Promise<Reply> {
      const checked = check(payload)
      if (!checked) return BAD_REQUEST
      try {
        const chosen = await service.request('PUT', '/settings/llm', checked.config)
        if (!chosen.ok) return chosen                                      // refused: no key is touched
        try {
          if (typeof checked.apiKey === 'string') keys.save(checked.preset, checked.apiKey)
          else if (checked.apiKey === null) keys.remove(checked.preset)
        } catch (error) {
          if (error instanceof KeyStoreError) return failure(0, KEY_ERRORS[error.code], error.message)   // the choice stays saved; the key was not
          throw error
        }
        const pushed = await pushKey(checked.preset)
        if (!pushed.ok) return pushed
        return await service.request('GET', '/settings/llm')
      } catch {
        return UNREACHABLE
      }
    },

    /** After the backend starts its memory is empty: push the key of the active provider (or clear it). Never throws. */
    async onBackendReady(): Promise<void> {
      try {
        const current = await service.request('GET', '/settings/llm')
        const active = current.ok && typeof current.body === 'object' && current.body !== null ? (current.body as { active?: { preset?: unknown } }).active : undefined
        if (active && typeof active.preset === 'string') await pushKey(active.preset)
      } catch {
        /* the backend went away again: it will be pushed after the next start */
      }
    },
  }
}
