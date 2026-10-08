// The "Edit limits" form of the Usage tab: five windows, a number of tokens and/or requests each. Pure: no screen, no backend.
import test from 'node:test'
import assert from 'node:assert/strict'
import { WINDOW_ORDER, formFromReport, isUsageFormDirty, payloadFromUsageForm, validateUsageForm, type UsageForm } from './usageForm.ts'
import type { UsageLimitRow, UsageReport } from '../api/types.ts'

const row = (window: string, kind: 'tokens' | 'requests', limit: number): UsageLimitRow =>
  ({ window, kind, limit, used: 0, percent: 0, reached: false, rolling: true, resets_at: null, resets_in_seconds: null, source: 'yours' })
const report = (limits: UsageLimitRow[]): UsageReport => ({
  provider: 'groq', model: 'm', now: 1, limits, limits_source: limits.length ? 'yours' : 'none', has_suggestion: false, published_note: null, provider_reported: [], closest: null,
  counts: { requests: 0, prompt_tokens: 0, thinking_tokens: 0, answer_tokens: 0, estimated_calls: 0, thinking_share: null },
})
const blank = (): UsageForm => formFromReport(report([]))
const with_ = (changes: Record<string, string>): UsageForm => {
  const form = blank()
  for (const [key, value] of Object.entries(changes)) {
    const [window, kind] = key.split('.') as [keyof UsageForm, 'tokens' | 'requests']
    form[window] = { ...form[window], [kind]: value }
  }
  return form
}

test('the windows come in this order', () => {
  assert.deepEqual([...WINDOW_ORDER], ['minute', 'hour', 'day', 'week', 'month'])
})

test('the form is filled from the limits the report shows, and is empty where there is no limit', () => {
  const form = formFromReport(report([row('minute', 'tokens', 8000), row('minute', 'requests', 30), row('day', 'tokens', 200000)]))
  assert.deepEqual(form.minute, { tokens: '8000', requests: '30' })
  assert.deepEqual(form.day, { tokens: '200000', requests: '' })
  assert.deepEqual(form.hour, { tokens: '', requests: '' })
  assert.deepEqual(Object.keys(form), [...WINDOW_ORDER])
})

test('the payload holds only the filled fields and only the windows that have one', () => {
  assert.deepEqual(payloadFromUsageForm(blank()), {})
  assert.deepEqual(payloadFromUsageForm(with_({ 'hour.tokens': '50000', 'week.requests': '5000' })), { hour: { tokens: 50000 }, week: { requests: 5000 } })
  assert.deepEqual(payloadFromUsageForm(with_({ 'day.tokens': '1', 'day.requests': '2' })), { day: { tokens: 1, requests: 2 } })
})

test('spaces and thousands separators are allowed in a number', () => {
  assert.deepEqual(payloadFromUsageForm(with_({ 'minute.tokens': ' 8,000 ', 'day.tokens': '200 000' })), { minute: { tokens: 8000 }, day: { tokens: 200000 } })
  assert.deepEqual(validateUsageForm(with_({ 'minute.tokens': '8,000' })), {})
})

test('a blank field is fine; anything but a whole number of at least 1 is refused with a sentence', () => {
  assert.deepEqual(validateUsageForm(blank()), {})
  for (const bad of ['0', '00', '-5', '1.5', '1e3', 'ten', '5 tokens', '+5', '١٢', '1,5,', '1,5', '1,50', '12,34,567', ',500', '0x10']) {
    assert.deepEqual(validateUsageForm(with_({ 'day.tokens': bad })), { 'day.tokens': 'Use a whole number of at least 1.' }, JSON.stringify(bad))
  }
})

test('a number that is too large is refused, at the same point as the backend', () => {
  assert.deepEqual(validateUsageForm(with_({ 'day.requests': '1000000000000' })), {})
  assert.deepEqual(validateUsageForm(with_({ 'day.requests': '1000000000001' })), { 'day.requests': 'That number is too large.' })
  assert.deepEqual(validateUsageForm(with_({ 'day.requests': '9'.repeat(400) })), { 'day.requests': 'That number is too large.' })
})

test('leading zeros do not matter, and a window the form does not know is ignored', () => {
  assert.deepEqual(payloadFromUsageForm(with_({ 'day.tokens': '007' })), { day: { tokens: 7 } })
  const odd = report([row('year', 'tokens', 5), row('day', 'tokens', 9)])
  assert.deepEqual(Object.keys(formFromReport(odd)), [...WINDOW_ORDER])
  assert.deepEqual(payloadFromUsageForm(formFromReport(odd)), { day: { tokens: 9 } })
})

test('every bad field has its own message', () => {
  assert.deepEqual(Object.keys(validateUsageForm(with_({ 'minute.tokens': 'x', 'month.requests': '0', 'hour.tokens': '5' }))).sort(), ['minute.tokens', 'month.requests'])
})

test('the form is dirty only when its limits differ from the report (whatever the spacing)', () => {
  const current = report([row('minute', 'tokens', 8000), row('day', 'requests', 1000)])
  assert.equal(isUsageFormDirty(formFromReport(current), current), false)
  assert.equal(isUsageFormDirty(with_({ 'minute.tokens': '8,000', 'day.requests': ' 1000 ' }), current), false)
  assert.equal(isUsageFormDirty(with_({ 'minute.tokens': '8001', 'day.requests': '1000' }), current), true)
  assert.equal(isUsageFormDirty(with_({ 'minute.tokens': '8000' }), current), true)                       // a limit was removed
  assert.equal(isUsageFormDirty(with_({ 'minute.tokens': '8000', 'day.requests': '1000', 'hour.tokens': '3' }), current), true)      // one was added
  assert.equal(isUsageFormDirty(blank(), report([])), false)
})
