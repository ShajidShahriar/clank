import type { Answer, ApiError, StartEvent } from './api/types'

export type BackendState = 'starting' | 'ready' | 'crashed' | 'failed' | 'stopped' | 'no_bridge'
export type BackendView = { state: BackendState, message: string, detail: string[] }

export type Stage = 'searching' | 'waiting' | 'thinking' | 'writing'

/** An answer while it is being written: where it is, since when (the window's own clock, in milliseconds), and what has arrived so far. */
export type Live = {
  stage: Stage
  stageStartedAt: number
  startedAt: number
  text: string
  thinkingPieces: number
  start: StartEvent | null
}

/**
 * One question in a conversation. pending: sent, nothing back yet. streaming: the answer is being written (`live`). done: the answer. error: it failed (with the half-written
 * text in `partial`, if there was any). stopped: the person stopped it (the half-written text is in `partial`, and nothing was saved).
 */
export type Entry = {
  id: string
  question: string
  state: 'pending' | 'streaming' | 'done' | 'error' | 'stopped'
  answer?: Answer
  error?: ApiError
  live?: Live
  partial?: string
  retryAfter?: number
}
