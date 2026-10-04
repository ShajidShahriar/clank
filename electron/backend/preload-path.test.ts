// Task 8.7: which built preload script to load. The template asks for `preload.mjs`, but this project's build writes `preload.js` (the package is not
// "type": "module"), so the preload was never loaded: nobody noticed because the mock window never used it. The window's bridge to the backend needs it.
import test from 'node:test'
import assert from 'node:assert/strict'
import { preloadFile } from './preload-path.ts'

const files = (present: Record<string, number>) => ({
  exists: (p: string) => p in present,
  modified: (p: string) => present[p],
})

test('it finds preload.js when that is what the build wrote', () => {
  assert.equal(preloadFile('/app/dist-electron', files({ '/app/dist-electron/preload.js': 5 })), '/app/dist-electron/preload.js')
})

test('it finds preload.mjs when that is what the build wrote', () => {
  assert.equal(preloadFile('/app/dist-electron', files({ '/app/dist-electron/preload.mjs': 5 })), '/app/dist-electron/preload.mjs')
})

test('when both exist the newest one wins (the other is left over from an older build)', () => {
  assert.equal(preloadFile('/d', files({ '/d/preload.js': 9, '/d/preload.mjs': 5 })), '/d/preload.js')
  assert.equal(preloadFile('/d', files({ '/d/preload.js': 5, '/d/preload.mjs': 9 })), '/d/preload.mjs')
})

test('when neither exists it says where it looked', () => {
  assert.throws(() => preloadFile('/nowhere', files({})), /preload.*\/nowhere/)
})
