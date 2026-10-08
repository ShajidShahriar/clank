// The words and the tone of the usage meter and the Usage tab. Pure: the same report always gives the same sentences.
import test from 'node:test'
import assert from 'node:assert/strict'
import { countsLines, formatCount, formatDuration, limitText, meterLabel, meterTone, providerRowText, refreshDelayMs } from './usage.ts'
import type { UsageClosest, UsageLimitRow, UsageProviderRow, UsageReport } from '../api/types.ts'

const closest = (over: Partial<UsageClosest> = {}): UsageClosest => ({ source: 'yours', window: 'minute', kind: 'tokens', limit: 8000, used: 6000, percent: 75, reached: false, resets_in_seconds: 30, ...over })
const limit = (over: Partial<UsageLimitRow> = {}): UsageLimitRow => ({ window: 'minute', kind: 'tokens', limit: 8000, used: 6000, percent: 75, reached: false, rolling: true, resets_at: 1, resets_in_seconds: 30, source: 'yours', ...over })
const report = (over: Partial<UsageReport> = {}): UsageReport => ({
  provider: 'groq', model: 'm', now: 1, limits: [], limits_source: 'none', has_suggestion: false, published_note: null, provider_reported: [], closest: null,
  counts: { requests: 0, prompt_tokens: 0, thinking_tokens: 0, answer_tokens: 0, estimated_calls: 0, thinking_share: null }, ...over,
})

test('numbers get thousands separators', () => {
  assert.equal(formatCount(0), '0')
  assert.equal(formatCount(999), '999')
  assert.equal(formatCount(8000), '8,000')
  assert.equal(formatCount(1234567), '1,234,567')
  assert.equal(formatCount(1234.6), '1,235')
  assert.equal(formatCount(0.4), '0')
})

test('a wait is rounded UP and written in the unit that reads best', () => {
  const cases: Array<[number, string]> = [[0, 'now'], [0.2, '1 s'], [1, '1 s'], [40.1, '41 s'], [59.5, '1 min'], [60, '1 min'], [61, '2 min'], [600, '10 min'], [3599, '60 min'], [3600, '1 h'],
    [4800, '1 h 20 min'], [86399, '24 h'], [86400, '1 d'], [90000, '1 d 1 h'], [90001, '1 d 2 h'], [172799, '2 d'], [172800, '2 d']]
  for (const [seconds, text] of cases) assert.equal(formatDuration(seconds), text, String(seconds))
})

test('a wait that is not a number is not shown as one', () => {
  for (const bad of [NaN, Infinity, -5, null, undefined]) assert.equal(formatDuration(bad as never), '')
})

test('the tone is quiet under 75, warning from 75, danger from 90 and when the limit is reached', () => {
  const tone = (percent: number, reached = false) => meterTone(closest({ percent, reached }))
  assert.equal(tone(0), 'quiet')
  assert.equal(tone(74.9), 'quiet')
  assert.equal(tone(75), 'warn')
  assert.equal(tone(89.9), 'warn')
  assert.equal(tone(90), 'danger')
  assert.equal(tone(100), 'danger')
  assert.equal(tone(10, true), 'danger')
  assert.equal(tone(NaN), 'quiet')
})

test('the meter says the percent, what it is a percent of, and never shows 100 before the limit is reached', () => {
  assert.deepEqual(meterLabel(closest()), { text: '75% · tokens per minute', tone: 'warn', title: '75% of your tokens limit this minute: 6,000 of 8,000. It frees up in 30 s.' })
  assert.equal(meterLabel(closest({ percent: 99.9 })).text.startsWith('99%'), true)
  assert.equal(meterLabel(closest({ percent: 12.4, window: 'day', kind: 'requests', used: 12, limit: 100, resets_in_seconds: 3600 })).text, '12% · requests per day')
})

test('a limit that is reached says so and when it resets', () => {
  assert.equal(meterLabel(closest({ percent: 100, reached: true, resets_in_seconds: 40 })).text, 'Limit reached · resets in 40 s')
  assert.equal(meterLabel(closest({ percent: 130, reached: true, resets_in_seconds: null })).text, 'Limit reached')
  assert.equal(meterLabel(closest({ percent: 130, reached: true, resets_in_seconds: null })).tone, 'danger')
})

test('the meter names where the number comes from', () => {
  assert.match(meterLabel(closest({ source: 'provider' })).title, /reported by the provider/)
  assert.match(meterLabel(closest({ source: 'published' })).title, /published/)
  assert.doesNotMatch(meterLabel(closest({ source: 'yours' })).title, /published|provider/)
  assert.match(meterLabel(closest({ source: 'provider', window: null })).text, /^75% · tokens$/)
})

test('a limit that frees up on its own says so for a rolling window, and says it starts again for a calendar one; the provider says full again', () => {
  assert.match(meterLabel(closest({ window: 'hour', resets_in_seconds: 90 })).title, /It frees up in 2 min\.$/)
  assert.match(meterLabel(closest({ window: 'day', resets_in_seconds: 90 })).title, /It starts again in 2 min\.$/)
  assert.match(meterLabel(closest({ source: 'provider', window: 'minute', resets_in_seconds: 20 })).title, /It is full again in 20 s\.$/)
  assert.doesNotMatch(meterLabel(closest({ resets_in_seconds: null })).title, /It /)
})

test('the meter refreshes every 5 seconds while a minute limit is the closest, otherwise every minute', () => {
  assert.equal(refreshDelayMs(report({ closest: closest({ window: 'minute' }) })), 5000)
  assert.equal(refreshDelayMs(report({ closest: closest({ window: 'day' }) })), 60000)
  assert.equal(refreshDelayMs(report({ closest: closest({ window: 'hour' }) })), 60000)
  assert.equal(refreshDelayMs(report({ closest: closest({ window: null }) })), 60000)
  assert.equal(refreshDelayMs(report()), 60000)
  assert.equal(refreshDelayMs(null), 60000)
})

test('a limit row reads as a sentence with what was used, the percent and when it frees up', () => {
  assert.equal(limitText(limit()), 'Tokens this minute: 6,000 of 8,000 (75%). Frees up in 30 s.')
  assert.equal(limitText(limit({ window: 'day', kind: 'requests', used: 12, limit: 1000, percent: 1.2, rolling: false, resets_in_seconds: 3600 })), 'Requests today: 12 of 1,000 (1%). Starts again in 1 h.')
  assert.equal(limitText(limit({ window: 'hour', used: 0, percent: 0, resets_in_seconds: null })), 'Tokens this hour: 0 of 8,000 (0%).')
  assert.equal(limitText(limit({ percent: 1.9, used: 152 })), 'Tokens this minute: 152 of 8,000 (1%). Frees up in 30 s.')
  assert.equal(limitText(limit({ window: 'week', percent: 130, used: 10400, reached: true, rolling: false, resets_in_seconds: 86400 })), 'Tokens this week: 10,400 of 8,000 (130%). Limit reached. Starts again in 1 d.')
  assert.equal(limitText(limit({ window: 'month', rolling: false })), 'Tokens this month: 6,000 of 8,000 (75%). Starts again in 30 s.')
})

test("the provider's own row says it is the provider's, with how old the number is", () => {
  const row: UsageProviderRow = { kind: 'tokens', window: 'minute', limit: 8000, remaining: 6000, used: 2000, percent: 25, full_again_in_seconds: 20, age_seconds: 4 }
  assert.equal(providerRowText(row), 'Tokens per minute: 2,000 of 8,000 used. Full again in 20 s. As of 4 s ago.')
  assert.match(providerRowText({ ...row, age_seconds: 0.5 }), /As of just now\.$/)
  assert.match(providerRowText({ ...row, age_seconds: 1 }), /As of 1 s ago\.$/)
  assert.equal(providerRowText({ ...row, window: null, full_again_in_seconds: null, age_seconds: 0 }), 'Tokens: 2,000 of 8,000 used. As of just now.')
  assert.equal(providerRowText({ ...row, kind: 'requests', window: 'day', full_again_in_seconds: 0, age_seconds: 90 }), 'Requests per day: 2,000 of 8,000 used. Full again. As of 2 min ago.')
})

test('the thinking share is rounded to the nearest percent', () => {
  const counts = (share: number) => report({ counts: { requests: 2, prompt_tokens: 1, thinking_tokens: 1, answer_tokens: 1, estimated_calls: 0, thinking_share: share } }).counts
  assert.equal(countsLines(counts(0.3367))[2], 'Thinking is 34% of what the model wrote')
  assert.equal(countsLines(counts(0))[2], 'Thinking is 0% of what the model wrote')
})

test('the counts say how many calls, the tokens, the thinking share and how many are estimates', () => {
  const lines = countsLines(report({ counts: { requests: 3, prompt_tokens: 300, thinking_tokens: 40, answer_tokens: 80, estimated_calls: 1, thinking_share: 0.3333 } }).counts)
  assert.deepEqual(lines, ['3 calls to the model', 'Prompt 300 · thinking 40 · answer 80 tokens', 'Thinking is 33% of what the model wrote', '1 call is an estimate'])
  assert.deepEqual(countsLines(report({ counts: { requests: 1, prompt_tokens: 0, thinking_tokens: 0, answer_tokens: 0, estimated_calls: 0, thinking_share: null } }).counts), ['1 call to the model', 'Prompt 0 · thinking 0 · answer 0 tokens'])
  assert.deepEqual(countsLines(report({ counts: { requests: 0, prompt_tokens: 0, thinking_tokens: 0, answer_tokens: 0, estimated_calls: 0, thinking_share: null } }).counts), ['Nothing has been counted yet'])
  assert.equal(countsLines(report({ counts: { requests: 5, prompt_tokens: 1, thinking_tokens: 1, answer_tokens: 1, estimated_calls: 4, thinking_share: 0.5 } }).counts).at(-1), '4 calls are estimates')
})
