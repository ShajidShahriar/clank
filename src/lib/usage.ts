// The words and the tone of the usage meter and the Usage tab. Pure: the same report always gives the same sentences.
import type { UsageClosest, UsageCounts, UsageLimitRow, UsageProviderRow, UsageReport } from '../api/types.ts'

export type MeterTone = 'quiet' | 'warn' | 'danger'

const THIS: Record<string, string> = { minute: 'this minute', hour: 'this hour', day: 'today', week: 'this week', month: 'this month' }
const PER: Record<string, string> = { minute: 'per minute', hour: 'per hour', day: 'per day', week: 'per week', month: 'per month' }
const ROLLING = new Set(['minute', 'hour'])
const WARN_AT = 75
const DANGER_AT = 90

export function formatCount(n: number): string {
  return Math.round(n).toLocaleString('en-US')
}

/** A wait, rounded UP (a wait is never shown shorter than it is), in the unit that reads best. Not a number: nothing. */
export function formatDuration(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return ''
  const sec = Math.ceil(seconds)
  if (sec === 0) return 'now'
  if (sec < 60) return `${sec} s`
  if (sec < 3600) return `${Math.ceil(sec / 60)} min`
  if (sec < 86400) {
    let hours = Math.floor(sec / 3600)
    let minutes = Math.ceil((sec % 3600) / 60)
    if (minutes === 60) { hours += 1; minutes = 0 }
    return minutes === 0 ? `${hours} h` : `${hours} h ${minutes} min`
  }
  let days = Math.floor(sec / 86400)
  let hours = Math.ceil((sec % 86400) / 3600)
  if (hours === 24) { days += 1; hours = 0 }
  return hours === 0 ? `${days} d` : `${days} d ${hours} h`
}

const shown = (percent: number): number => Math.floor(percent)         // never rounds up: 99.9 is not shown as 100 before the limit is reached
const kindWord = (kind: string): string => (kind === 'requests' ? 'requests' : 'tokens')
const capital = (text: string): string => text.charAt(0).toUpperCase() + text.slice(1)
const isNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)

export function meterTone(closest: Pick<UsageClosest, 'percent' | 'reached'>): MeterTone {
  if (closest.reached) return 'danger'
  if (!isNumber(closest.percent)) return 'quiet'
  if (closest.percent >= DANGER_AT) return 'danger'
  return closest.percent >= WARN_AT ? 'warn' : 'quiet'
}

function whose(source: UsageClosest['source']): string {
  return source === 'provider' ? 'the' : source === 'published' ? 'the published' : 'your'
}

/** What the meter beside the question bar shows: a short text, its tone, and a longer sentence for when the pointer rests on it. */
export function meterLabel(closest: UsageClosest): { text: string, tone: MeterTone, title: string } {
  const tone = meterTone(closest)
  const kind = kindWord(closest.kind)
  const when = closest.window ? THIS[closest.window] ?? closest.window : ''
  const per = closest.window ? PER[closest.window] ?? '' : ''
  const text = closest.reached
    ? (closest.resets_in_seconds === null ? 'Limit reached' : `Limit reached · resets in ${formatDuration(closest.resets_in_seconds)}`)
    : `${shown(closest.percent)}% · ${kind}${per ? ' ' + per : ''}`
  const wait = closest.resets_in_seconds === null ? ''
    : closest.source === 'provider' ? ` It is full again in ${formatDuration(closest.resets_in_seconds)}.`
      : ` It ${closest.window !== null && ROLLING.has(closest.window) ? 'frees up' : 'starts again'} in ${formatDuration(closest.resets_in_seconds)}.`
  const from = closest.source === 'provider' ? ', reported by the provider' : ''
  const title = `${shown(closest.percent)}% of ${whose(closest.source)} ${kind} limit${when ? ' ' + when : ''}${from}: ${formatCount(closest.used)} of ${formatCount(closest.limit)}.${wait}`
  return { text, tone, title }
}

/** How soon to ask again: every 5 seconds while a limit per minute is the closest one (it changes fast), otherwise every minute. */
export function refreshDelayMs(report: UsageReport | null): number {
  return report?.closest?.window === 'minute' ? 5000 : 60000
}

export function limitText(row: UsageLimitRow): string {
  const reset = row.resets_in_seconds === null ? '' : ` ${row.rolling ? 'Frees up' : 'Starts again'} in ${formatDuration(row.resets_in_seconds)}.`
  return `${capital(kindWord(row.kind))} ${THIS[row.window] ?? row.window}: ${formatCount(row.used)} of ${formatCount(row.limit)} (${shown(row.percent)}%).${row.reached ? ' Limit reached.' : ''}${reset}`
}

export function providerRowText(row: UsageProviderRow): string {
  const per = row.window ? ` ${PER[row.window] ?? ''}`.trimEnd() : ''
  const full = row.full_again_in_seconds === null ? '' : row.full_again_in_seconds <= 0 ? ' Full again.' : ` Full again in ${formatDuration(row.full_again_in_seconds)}.`
  const age = row.age_seconds < 1 ? 'just now' : `${formatDuration(row.age_seconds)} ago`
  return `${capital(kindWord(row.kind))}${per}: ${formatCount(row.used)} of ${formatCount(row.limit)} used.${full} As of ${age}.`
}

export function countsLines(counts: UsageCounts): string[] {
  if (counts.requests === 0) return ['Nothing has been counted yet']
  const lines = [`${formatCount(counts.requests)} ${counts.requests === 1 ? 'call' : 'calls'} to the model`,
    `Prompt ${formatCount(counts.prompt_tokens)} · thinking ${formatCount(counts.thinking_tokens)} · answer ${formatCount(counts.answer_tokens)} tokens`]
  if (counts.thinking_share !== null) lines.push(`Thinking is ${Math.round(counts.thinking_share * 100)}% of what the model wrote`)
  if (counts.estimated_calls > 0) lines.push(counts.estimated_calls === 1 ? '1 call is an estimate' : `${formatCount(counts.estimated_calls)} calls are estimates`)
  return lines
}
