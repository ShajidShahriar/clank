import type { Answer, ApiError } from './api/types'

export type BackendState = 'starting' | 'ready' | 'crashed' | 'failed' | 'stopped' | 'no_bridge'
export type BackendView = { state: BackendState, message: string, detail: string[] }

/** One question in a project's conversation: pending while the answer is being made, then done with the answer, or an error. */
export type Entry = {
  id: string
  question: string
  state: 'pending' | 'done' | 'error'
  answer?: Answer
  error?: ApiError
}
