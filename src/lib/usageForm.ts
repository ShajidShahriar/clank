// The "Edit limits" form of the Usage tab: five windows, a number of tokens and/or requests each. Pure: no screen, no backend. The backend checks everything again.
import type { UsageLimits, UsageReport } from '../api/types.ts'

export const WINDOW_ORDER = ['minute', 'hour', 'day', 'week', 'month'] as const
export type UsageWindow = typeof WINDOW_ORDER[number]
export type UsageForm = Record<UsageWindow, { tokens: string, requests: string }>
export type UsageFormErrors = Record<string, string>                     // keyed `<window>.<kind>`, for example `day.tokens`

const KINDS = ['tokens', 'requests'] as const
const MAX_LIMIT = 10 ** 12                                               // the same ceiling as the backend (usage_limits.MAX_LIMIT)
const WHOLE = /^[0-9]+$/

export function formFromReport(report: UsageReport): UsageForm {
  const form = Object.fromEntries(WINDOW_ORDER.map((window) => [window, { tokens: '', requests: '' }])) as UsageForm
  for (const limit of report.limits) {
    if ((WINDOW_ORDER as readonly string[]).includes(limit.window)) form[limit.window as UsageWindow] = { ...form[limit.window as UsageWindow], [limit.kind]: String(limit.limit) }
  }
  return form
}

const GROUPED = /^[0-9]{1,3}(,[0-9]{3})+$/

/** What was typed, without the spaces and the thousands commas people put in big numbers. A comma anywhere else (1,5 may be a decimal in some countries) is left in, so the number is refused. */
const cleaned = (text: string): string => {
  const typed = text.replace(/\s/g, '')
  return GROUPED.test(typed) ? typed.replace(/,/g, '') : typed
}

function parse(text: string): { kind: 'none' } | { kind: 'number', value: number } | { kind: 'bad' } | { kind: 'large' } {
  const typed = cleaned(text)
  if (typed === '') return { kind: 'none' }
  if (!WHOLE.test(typed)) return { kind: 'bad' }
  const value = Number(typed)                                           // (a very long number becomes Infinity, which is too large; leading zeros do not matter)
  if (value < 1) return { kind: 'bad' }
  return value > MAX_LIMIT ? { kind: 'large' } : { kind: 'number', value }
}

export function validateUsageForm(form: UsageForm): UsageFormErrors {
  const errors: UsageFormErrors = {}
  for (const window of WINDOW_ORDER) {
    for (const kind of KINDS) {
      const parsed = parse(form[window][kind])
      if (parsed.kind === 'bad') errors[`${window}.${kind}`] = 'Use a whole number of at least 1.'
      else if (parsed.kind === 'large') errors[`${window}.${kind}`] = 'That number is too large.'
    }
  }
  return errors
}

/** What is sent: only the filled fields, only the windows that have one. Call it only when `validateUsageForm` found nothing. */
export function payloadFromUsageForm(form: UsageForm): UsageLimits {
  const limits: UsageLimits = {}
  for (const window of WINDOW_ORDER) {
    const entry: { tokens?: number, requests?: number } = {}
    for (const kind of KINDS) {
      const parsed = parse(form[window][kind])
      if (parsed.kind === 'number') entry[kind] = parsed.value
    }
    if (Object.keys(entry).length > 0) limits[window] = entry
  }
  return limits
}

export function isUsageFormDirty(form: UsageForm, report: UsageReport): boolean {
  return JSON.stringify(payloadFromUsageForm(form)) !== JSON.stringify(payloadFromUsageForm(formFromReport(report)))
}
