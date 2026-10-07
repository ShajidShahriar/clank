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
}

/** The lines of one project file, from POST /projects/{id}/source. `lines[i]` is line `start_line + i`. */
export type SourceView = { path: string, start_line: number, end_line: number, total_lines: number, stale: boolean, lines: string[] }

export type ConversationSummary = { id: number, title: string | null, created_at: string, updated_at: string, message_count: number }
/** The answer's extras as saved: everything of an Answer except its text, which is the message's `content`. Checked when it is turned back into an Answer. */
export type SavedMessage = { id: number, role: 'user' | 'assistant', content: string, meta: Record<string, unknown> | null, created_at: string }
export type Conversation = { id: number, title: string | null, created_at: string, updated_at: string, messages: SavedMessage[] }

export type Health = { status: string, embedder: string, model: string | null, detail: string | null }

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
