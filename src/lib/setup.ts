// What the first-run card says. Pure (no React, no browser), tested in setup.test.ts. Erasable TypeScript only.
import type { Health, PullStatus } from '../api/types.ts'

export type SetupView =
  | { kind: 'none' }
  | { kind: 'ollama_down' }
  | { kind: 'model_missing' }
  | { kind: 'pulling', percent: number | null, message: string | null, summary: string | null }
  | { kind: 'pull_failed', error: string }
  | { kind: 'other', detail: string }

const UNKNOWN_FAILURE = 'The download stopped and Ollama did not say why. Try again.'
const UNKNOWN_PROBLEM = 'Clank could not start its search model. Check that Ollama is running, then check again.'

/** Which card to show, from the backend's health and the state of the model download. Nothing while the search model is ready, warming, or not yet known. */
export function setupView(health: Health | null, pull: PullStatus | null): SetupView {
  if (!health || health.embedder === 'ready' || health.embedder === 'warming') return { kind: 'none' }
  if (pull?.state === 'pulling') return { kind: 'pulling', percent: pullPercent(pull), message: pull.message, summary: pullSummary(pull) }
  if (health.problem === 'ollama_unavailable') return { kind: 'ollama_down' }
  if (health.problem === 'model_not_found') {
    return pull?.state === 'failed' ? { kind: 'pull_failed', error: pull.error || UNKNOWN_FAILURE } : { kind: 'model_missing' }
  }
  return { kind: 'other', detail: health.detail || UNKNOWN_PROBLEM }
}

/** A whole number from 0 to 100, or null while the size is not known. */
export function pullPercent(pull: PullStatus): number | null {
  return pull.percent === null ? null : Math.min(100, Math.max(0, Math.round(pull.percent)))
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = -1
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

/** "237 MB of 640 MB", or null while either number is not known. */
export function pullSummary(pull: PullStatus): string | null {
  if (pull.completed === null || pull.total === null || pull.total <= 0) return null
  return `${formatBytes(pull.completed)} of ${formatBytes(pull.total)}`
}
