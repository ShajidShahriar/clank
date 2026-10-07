import test from 'node:test'
import assert from 'node:assert/strict'
import { themeSource } from './theme.ts'

test('the window may ask for light, dark or the system theme', () => {
  assert.equal(themeSource('light'), 'light')
  assert.equal(themeSource('dark'), 'dark')
  assert.equal(themeSource('system'), 'system')
})

test('anything else is the system theme (never a crash, never an odd value for the OS)', () => {
  for (const bad of [null, undefined, '', 'LIGHT', 'blue', 5, {}, [], ['dark']]) assert.equal(themeSource(bad), 'system')
})
