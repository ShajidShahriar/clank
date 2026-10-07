// Streaming answers, step 1.4, part 3: when the list of messages should follow new text. A person who scrolled up to re-read must not be pulled to the bottom every
// time a word arrives; a person who is at the bottom (or very near it) should keep seeing the newest text. Pure.
import test from 'node:test'
import assert from 'node:assert/strict'
import { FOLLOW_MARGIN, isNearBottom } from './scroll.ts'

const box = (scrollTop: number, clientHeight = 500, scrollHeight = 2000) => ({ scrollTop, clientHeight, scrollHeight })

test('at the very bottom the list follows', () => {
  assert.equal(isNearBottom(box(1500)), true)
})

test('a little above the bottom still follows (a line or two of slack)', () => {
  assert.equal(isNearBottom(box(1500 - FOLLOW_MARGIN)), true)
})

test('further up than the margin does not follow', () => {
  assert.equal(isNearBottom(box(1500 - FOLLOW_MARGIN - 1)), false)
  assert.equal(isNearBottom(box(0)), false)
})

test('a list shorter than its box follows', () => {
  assert.equal(isNearBottom(box(0, 500, 300)), true)
  assert.equal(isNearBottom(box(0, 500, 500)), true)
})

test('a bounce past the end (negative distance) follows', () => {
  assert.equal(isNearBottom(box(1520)), true)
})

test('numbers that cannot be real follow, so the newest text is never hidden by a glitch', () => {
  for (const bad of [box(NaN), box(0, NaN), box(0, 500, NaN), box(Infinity)]) assert.equal(isNearBottom(bad), true)
})

test('the margin is about two lines of text', () => {
  assert.ok(FOLLOW_MARGIN >= 24 && FOLLOW_MARGIN <= 96)
})
