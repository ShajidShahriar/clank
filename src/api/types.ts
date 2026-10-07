// What the backend sends, as the window uses it. Kept in step with backend/routes_*.py and checked at run time in client.ts.
export type IndexState = 'idle' | 'running' | 'cancelling' | 'done' | 'stopped' | 'cancelled' | 'failed'

export type Project = {
  id: number
  name: string
  path: string
  created_at: string
  indexed: boolean
  files: number
  chunks: number
  flagged_files: number
  index_state: string
}

export type IndexStatus = {
  state: IndexState
  project_id: number
  files_done?: number
  files_total?: number
  current_file?: string | null
  message?: string | null
  skipped?: Array<{ path: string, reason: string }>
  error?: { code: string, message: string } | null
  stopped_kind?: string | null
}

export type Source = {
  path: string
  symbol: string | null
  parent: string | null
  kind: string
  start_line: number
  end_line: number
  score: number
  stale: boolean
  complete: boolean
  narrowed: boolean
}

/** How long each stage of an answer took, in whole milliseconds; null for a stage that did not happen (a model that does not think has no thinking time). */
export type Timings = { search_ms: number | null, wait_ms: number | null, thinking_ms: number | null, writing_ms: number | null, total_ms: number | null }
export type ThinkingInfo = { seen: boolean, pieces: number }
/** Tokens spent thinking and answering. `estimated` is true when anything here is a count or an estimate instead of the service's own number. */
export type TokenInfo = { thinking: number | null, answer: number | null, estimated: boolean }

export type Answer = {
  answer: string
  truncated: boolean
  finish_reason: string | null
  notice: string | null
  llm_called: boolean
  model: string | null
  profile: string
  usage: { prompt_tokens: number | null, completion_tokens: number | null } | null
  sent_off_machine: boolean
  sources: Source[]
  dropped: Array<{ path: string, start_line: number, end_line: number, score: number }>
  hidden_files: Array<{ path: string, reason: string }>
  stale_files: string[]
  deleted_files: string[]
  context_tokens_used: number
  context_budget: number
  over_budget: boolean
  best_score: number | null
  k: number
  ranking_note: string | null
  calibration_note: string | null
  conversation_id?: number | null         // set when the question was asked inside a saved conversation
  // Not in answers saved before these existed:
  timings?: Timings
  thinking?: ThinkingInfo
  tokens?: TokenInfo
}

// ---- an answer written live (backend/routes_answer.py `/answer/stream`, through the desktop app's stream door). Every event is checked in client.ts before it is used.
export type StartEvent = {
  type: 'start'
  stage: 'waiting'
  search_ms: number
  llm_called: boolean
  profile: string
  sent_off_machine: boolean
  conversation_id: number | null
  sources: Source[]
  dropped: Answer['dropped']
  hidden_files: Answer['hidden_files']
  stale_files: string[]
  deleted_files: string[]
  context_tokens_used: number
  context_budget: number
  over_budget: boolean
  best_score: number | null
  k: number
  ranking_note: string | null
  calibration_note: string | null
}
export type StageEvent = { type: 'stage', stage: 'thinking' | 'writing', at_ms: number }
export type ThinkingEvent = { type: 'thinking', pieces: number }
export type DeltaEvent = { type: 'delta', text: string }
export type DoneEvent = Answer & { type: 'done', timings: Timings, thinking: ThinkingInfo, tokens: TokenInfo, conversation_id: number | null, saved: boolean }
export type ErrorEvent = { type: 'error', error: { code: string, message: string, status: number }, retry_after?: number }
export type CancelledEvent = { type: 'cancelled' }
export type StreamEvent = StartEvent | StageEvent | ThinkingEvent | DeltaEvent | DoneEvent | ErrorEvent | CancelledEvent

/** `ok: true`: the stream ran and its last event (done, error or cancelled) was handed over. `ok: false`: the start was refused or could not be made, and no event will ever come. */
export type StreamEnd = { ok: true } | { ok: false, error: ApiError }
export type StreamHandle = { id: string, stop(): Promise<void>, finished: Promise<StreamEnd> }

/** The lines of one project file, from POST /projects/{id}/source. `lines[i]` is line `start_line + i`. */
export type SourceView = { path: string, start_line: number, end_line: number, total_lines: number, stale: boolean, lines: string[] }

export type ConversationSummary = { id: number, title: string | null, created_at: string, updated_at: string, message_count: number }
/** The answer's extras as saved: everything of an Answer except its text, which is the message's `content`. Checked when it is turned back into an Answer. */
export type SavedMessage = { id: number, role: 'user' | 'assistant', content: string, meta: Record<string, unknown> | null, created_at: string }
export type Conversation = { id: number, title: string | null, created_at: string, updated_at: string, messages: SavedMessage[] }

/** `problem` says WHY the search model is not ready (null when it is ready or still warming): ollama_unavailable, model_not_found or embedding_error. */
export type Health = { status: string, embedder: string, model: string | null, detail: string | null, problem: string | null }

/** The download of the search model through Ollama (backend/model_pull.py). `percent` is a whole number, or null until the size is known. */
export type PullStatus = {
  state: 'idle' | 'pulling' | 'done' | 'failed'
  model: string
  message: string | null
  percent: number | null
  completed: number | null
  total: number | null
  error: string | null
}

export type ApiError = { code: string, message: string, status: number }
export type Result<T> = { ok: true, data: T } | { ok: false, error: ApiError }

// ---- the answer-model settings (backend/routes_settings.py). A key is never a field of anything here: it goes through the desktop app's own door.
export type LlmActive = {
  preset: string
  label: string
  base_url: string
  model: string
  context_tokens: number
  max_output_tokens: number
  local: boolean
  takes_key: boolean
  key_optional: boolean
  key_set: boolean
  key_source: string | null
}

export type LlmPreset = {
  id: string
  label: string
  base_url: string
  model: string
  takes_key: boolean
  key_optional: boolean
  local: boolean
  base_url_editable: boolean
  context_tokens: number
  max_output_tokens: number
  note: string
}

export type LlmSettings = { active: LlmActive, presets: LlmPreset[] }
export type LlmTest = { ok: boolean, model: string, latency_ms: number, finish_reason: string | null, reply: string }
