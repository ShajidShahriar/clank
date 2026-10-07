// What the window shows while an answer is being written, and the summary line it keeps afterwards. Pure (no React, no clock of its own: the time is passed in), tested in
// liveAnswer.test.ts. Erasable TypeScript only, so that `node --test` can run it.
import type { Answer, StreamEvent } from '../api/types.ts'
import type { Entry, Live } from '../types.ts'

/** Seconds for people: 0.4 s, 3.2 s, 42 s, 1 min 5 s. Anything that cannot be a duration is 0.0 s. */
export function formatSeconds(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '0.0 s'
  const tenths = Math.round(ms / 100)
  if (tenths < 100) return `${(tenths / 10).toFixed(1)} s`
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return rest === 0 ? `${minutes} min` : `${minutes} min ${rest} s`
}

export function formatTokens(count: number, estimated: boolean): string {
  return `${estimated ? 'about ' : ''}${count.toLocaleString('en-US')} ${count === 1 ? 'token' : 'tokens'}`
}

export function initialLive(now: number): Live {
  return { stage: 'searching', stageStartedAt: now, startedAt: now, text: '', thinkingPieces: 0, start: null }
}

/** The live state after one event. The last events (done, error, cancelled) are the entry's business, not the live state's. Never changes its input. */
export function applyEvent(live: Live, event: StreamEvent, now: number): Live {
  switch (event.type) {
    case 'start':
      return { ...live, stage: 'waiting', stageStartedAt: now, start: event }
    case 'stage':
      return event.stage === live.stage ? live : { ...live, stage: event.stage, stageStartedAt: now }
    case 'thinking':
      return { ...live, thinkingPieces: Math.max(live.thinkingPieces, event.pieces) }
    case 'delta':
      return { ...live, text: live.text + event.text, ...(live.stage === 'writing' ? {} : { stage: 'writing' as const, stageStartedAt: now }) }
    default:
      return live
  }
}

/** The line shown while waiting: where the answer is and for how long. A model that does not think never shows a thinking stage. */
export function stageLabel(live: Live, now: number): string {
  const elapsed = formatSeconds(now - live.stageStartedAt)
  switch (live.stage) {
    case 'searching': return 'Searching the code…'
    case 'waiting': return `Waiting for the model… ${elapsed}`
    case 'thinking': return `Thinking… ${elapsed}${live.thinkingPieces > 0 ? ` · ${formatTokens(live.thinkingPieces, true)}` : ''}`
    case 'writing': return 'Writing…'
  }
}

/** "Searched 0.4 s · Thought 3.2 s, 412 tokens · Answer 268 tokens · 5.1 s total", or null for an answer saved before these numbers existed. */
export function summaryLine(answer: Answer): string | null {
  const { timings, thinking, tokens } = answer
  if (!timings && !thinking && !tokens) return null
  const parts: string[] = []
  if (timings?.search_ms != null) parts.push(`Searched ${formatSeconds(timings.search_ms)}`)
  if (answer.llm_called) {
    const thoughtMs = timings?.thinking_ms ?? null
    const thoughtTokens = tokens?.thinking ?? null
    if (thoughtMs !== null || thoughtTokens !== null) {
      const bits: string[] = []
      if (thoughtMs !== null) bits.push(formatSeconds(thoughtMs))
      if (thoughtTokens !== null) bits.push(formatTokens(thoughtTokens, tokens?.estimated ?? false))
      parts.push(`Thought ${bits.join(', ')}`)
    } else {
      parts.push('No thinking')
    }
    if (tokens?.answer != null) parts.push(`Answer ${formatTokens(tokens.answer, tokens.estimated)}`)
    if (timings?.total_ms != null) parts.push(`${formatSeconds(timings.total_ms)} total`)
  }
  return parts.join(' · ')
}

export function startEntry(id: string, question: string, now: number): Entry {
  return { id, question, state: 'pending', live: initialLive(now) }
}

/** The entry after one event. An entry that already ended does not change. Never changes its input. */
export function entryAfterEvent(entry: Entry, event: StreamEvent, now: number): Entry {
  if (entry.state === 'done' || entry.state === 'error' || entry.state === 'stopped') return entry
  const text = entry.live?.text ?? ''
  switch (event.type) {
    case 'done': {
      const { type, saved, ...answer } = event
      void type; void saved
      return { id: entry.id, question: entry.question, state: 'done', answer }
    }
    case 'error':
      return {
        id: entry.id, question: entry.question, state: 'error', error: event.error,
        ...(text ? { partial: text } : {}), ...(event.retry_after !== undefined ? { retryAfter: event.retry_after } : {}),
      }
    case 'cancelled':
      return { id: entry.id, question: entry.question, state: 'stopped', ...(text ? { partial: text } : {}) }
    default:
      return { ...entry, state: 'streaming', live: applyEvent(entry.live ?? initialLive(now), event, now) }
  }
}
