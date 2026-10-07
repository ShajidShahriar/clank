import test from 'node:test'
import assert from 'node:assert/strict'
import { oppositeTheme, parseTheme, resolveTheme } from './theme.ts'

test('only "light" and "dark" are a saved choice; anything else means none', () => {
  assert.equal(parseTheme('light'), 'light')
  assert.equal(parseTheme('dark'), 'dark')
  for (const bad of [null, undefined, '', 'Dark', 'system', 'auto', 1, {}, true]) assert.equal(parseTheme(bad), null)
})

test('with no saved choice the system decides; a saved choice always wins', () => {
  assert.equal(resolveTheme(null, true), 'dark')
  assert.equal(resolveTheme(null, false), 'light')
  assert.equal(resolveTheme('light', true), 'light')
  assert.equal(resolveTheme('dark', false), 'dark')
})

test('the toggle goes to the other theme', () => {
  assert.equal(oppositeTheme('light'), 'dark')
  assert.equal(oppositeTheme('dark'), 'light')
})
