// Which built preload script to load (task 8.7). Depending on the package's module type the build writes `preload.mjs` or `preload.js`; both are looked for,
// and when both exist the newest one is used (the other is left over from an older build).
import path from 'node:path'

export type FileOps = { exists(p: string): boolean, modified(p: string): number }

export function preloadFile(dir: string, ops: FileOps): string {
  const found = ['preload.mjs', 'preload.js'].map((name) => path.join(dir, name)).filter((p) => ops.exists(p))
  if (found.length === 0) throw new Error(`no preload script (preload.mjs or preload.js) found in ${dir}`)
  return found.sort((a, b) => ops.modified(b) - ops.modified(a))[0]
}
