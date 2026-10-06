// How the window turns backend data into words and pieces to draw. All pure (no React, no browser), so all of it is tested in present.test.ts;
// the components only draw what these functions return. Erasable TypeScript only, so that `node --test` can run it.
import type { Answer, ApiError, IndexStatus, Project, Source } from '../api/types.ts'

export type Part = { kind: 'text' | 'code', text: string, lang: string }

const OPENING_FENCE = /^\s*```([^\s`]*)\s*$/
const CLOSING_FENCE = /^\s*```\s*$/

/** Split an answer into prose and fenced code blocks. A fence that is never closed (an answer cut off mid-block) makes the rest code. Nothing is dropped. */
export function splitAnswer(text: string): Part[] {
  const parts: Part[] = []
  let kind: 'text' | 'code' = 'text'
  let lang = ''
  let buffer: string[] = []
  const flush = () => {
    const body = buffer.join('\n').replace(/^\n+|\n+$/g, '')      // only blank lines at the edges go: indentation inside code is kept
    if (body.trim() !== '') parts.push({ kind, text: body, lang: kind === 'code' ? lang : '' })
    buffer = []
  }
  for (const line of text.split('\n')) {
    if (kind === 'text') {
      const opening = OPENING_FENCE.exec(line)
      if (opening) {
        flush()
        kind = 'code'
        lang = opening[1]
      } else {
        buffer.push(line)
      }
    } else if (CLOSING_FENCE.test(line)) {
      flush()
      kind = 'text'
      lang = ''
    } else {
      buffer.push(line)
    }
  }
  flush()
  return parts
}

/** `path:start-end` with a plain hyphen (what the model is asked to write too), or `path:line` for one line. */
export function sourceLabel(source: Pick<Source, 'path' | 'start_line' | 'end_line'>): string {
  return source.start_line === source.end_line ? `${source.path}:${source.start_line}` : `${source.path}:${source.start_line}-${source.end_line}`
}

const number = (n: number) => n.toLocaleString('en-US')

export function tokenSummary(usage: Answer['usage']): string {
  if (!usage) return ''
  const pieces: string[] = []
  if (usage.prompt_tokens !== null) pieces.push(`${number(usage.prompt_tokens)} in`)
  if (usage.completion_tokens !== null) pieces.push(`${number(usage.completion_tokens)} out`)
  return pieces.join(' · ')
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many)

export function answerWarnings(answer: Answer): string[] {
  const warnings: string[] = []
  if (answer.sent_off_machine) warnings.push('Code excerpts were sent to a remote model.')
  if (answer.truncated) warnings.push(answer.notice ?? 'The answer was cut off by the length limit.')
  const stale = answer.stale_files.length
  if (stale) warnings.push(`${stale} ${plural(stale, 'file changed since it was', 'files changed since they were')} indexed, so some sources may be out of date.`)
  const deleted = answer.deleted_files.length
  if (deleted) warnings.push(`${deleted} ${plural(deleted, 'file no longer exists and was', 'files no longer exist and were')} left out.`)
  const hidden = answer.hidden_files.length
  if (hidden) warnings.push(`${hidden} ${plural(hidden, 'file could not be indexed and was', 'files could not be indexed and were')} left out.`)
  return warnings
}

export type Tone = 'neutral' | 'busy' | 'ok' | 'warn'

/** The one line shown under a project's name. A live index status (while one is running) wins over the project's own state from the list. */
export function projectStatus(project: Project, index?: IndexStatus): { label: string, tone: Tone } {
  const state = index?.state ?? project.index_state
  if (state === 'cancelling') return { label: 'Stopping…', tone: 'busy' }
  if (state === 'running') {
    const total = index?.files_total ?? 0
    return { label: total > 0 ? `Indexing ${index?.files_done ?? 0}/${total}` : 'Indexing…', tone: 'busy' }
  }
  if (state === 'stopped') return { label: 'Index stopped', tone: 'warn' }
  if (state === 'failed') return { label: 'Index failed', tone: 'warn' }
  if (state === 'cancelled') return { label: 'Index cancelled', tone: 'warn' }
  if (project.indexed) {
    const skipped = project.flagged_files > 0 ? ` · ${project.flagged_files} skipped` : ''
    return { label: `${project.files} ${plural(project.files, 'file', 'files')}${skipped}`, tone: 'ok' }
  }
  return { label: 'Not indexed', tone: 'neutral' }
}

/** How far a running index is, from 0 to 1, or null when it is not running or the total is not known yet. */
export function progressFraction(index: IndexStatus | undefined): number | null {
  if (!index || (index.state !== 'running' && index.state !== 'cancelling')) return null
  const total = index.files_total ?? 0
  if (total <= 0) return null
  return Math.min(1, Math.max(0, (index.files_done ?? 0) / total))
}

export type ErrorAction = 'allow_remote' | 'index' | 'restart_backend' | 'retry' | 'wait' | 'refresh' | 'settings' | 'none'

const ACTIONS: Record<string, ErrorAction> = {
  consent_required: 'allow_remote',
  not_indexed: 'index',
  index_out_of_date: 'index',
  backend_unavailable: 'restart_backend',
  project_not_found: 'refresh',
  llm_rate_limited: 'wait',
  ollama_unavailable: 'retry', llm_unavailable: 'retry', llm_timeout: 'retry', backend_timeout: 'retry', unknown_error: 'retry', bad_response: 'retry',
  llm_bad_response: 'retry', embedding_error: 'retry', llm_error: 'retry', llm_context_too_long: 'retry',
  llm_not_configured: 'settings', llm_auth_failed: 'settings', llm_model_not_found: 'settings', no_bridge: 'none', invalid_request: 'none', invalid_path: 'none',
  project_exists: 'none', project_busy: 'none', empty_question: 'none', model_not_found: 'none', bad_request: 'none', repo_not_found: 'none', no_index_running: 'none',
  invalid_settings: 'none', invalid_key: 'none', secure_storage_unavailable: 'none', key_save_failed: 'none', settings_failed: 'none', dialog_failed: 'none', forbidden: 'none',
}

/** What to show for an error and what the person can do next. The words are the backend's own (they are written for people). */
export function describeError(error: ApiError): { message: string, action: ErrorAction, retryAfterSeconds: number | null } {
  const action = ACTIONS[error.code] ?? 'retry'                      // an error never seen before can at least be tried again
  let retryAfterSeconds: number | null = null
  if (action === 'wait') {
    const seconds = /about (\d+) second/.exec(error.message)
    retryAfterSeconds = seconds ? Number(seconds[1]) : 60              // unknown: a minute, never "now"
  }
  return { message: error.message, action, retryAfterSeconds }
}
