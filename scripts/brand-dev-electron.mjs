// In development the Dock and the menu bar show the name and the icon of the Electron program that runs the app. This gives that copy of Electron (inside node_modules)
// the app's name and icon. It runs before `npm run dev` (the `predev` script) and on a Mac only. A packaged app does not need it: electron-builder names that one.
//
// Safe to run any time: a copy that already has the name and the icon is left alone; a copy that is running is not touched; and a failure only prints a warning, so
// `npm run dev` always starts. A reinstall of Electron brings the plain copy back, and the next `npm run dev` brands it again.
import { execFileSync } from 'node:child_process'
import { copyFileSync, existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

export const NAME = 'Clank'

/** What has to change, given what the copy has now. */
export function needed({ name, displayName, sameIcon }, wanted) {
  return { name: name !== wanted, displayName: displayName !== wanted, icon: !sameIcon }
}

/**
 * Does the work through `ops` (so it can be tested): read and set the two names, compare and copy the icon, say whether Electron runs from this copy, sign it again.
 * Returns what happened: `changed`, and `skipped` (a sentence, or null).
 */
export function brand(ops) {
  try {
    const todo = needed({ name: ops.read('CFBundleName'), displayName: ops.read('CFBundleDisplayName'), sameIcon: ops.sameIcon() }, NAME)
    if (!todo.name && !todo.displayName && !todo.icon) return { changed: false, skipped: null }
    if (ops.running()) return { changed: false, skipped: 'Electron is running from this copy, so its name and icon were not changed. Quit it and run npm run dev again.' }
    if (todo.name) ops.set('CFBundleName', NAME)
    if (todo.displayName) ops.set('CFBundleDisplayName', NAME)
    if (todo.icon) ops.copyIcon()
    ops.sign()                                                   // a changed bundle with its old signature does not start on Apple Silicon
    return { changed: true, skipped: null }
  } catch (problem) {
    return { changed: false, skipped: `The name and icon of the development copy of Electron could not be changed (${problem instanceof Error ? problem.message.split('\n')[0] : 'unknown problem'}). The app still starts.` }
  }
}

export function realOps(app, icon) {
  const plist = path.join(app, 'Contents', 'Info.plist')
  const target = path.join(app, 'Contents', 'Resources', 'electron.icns')
  const run = (file, args) => execFileSync(file, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  return {
    read: (key) => run('/usr/libexec/PlistBuddy', ['-c', `Print :${key}`, plist]).trim(),
    set: (key, value) => { run('/usr/libexec/PlistBuddy', ['-c', `Set :${key} ${value}`, plist]) },
    sameIcon: () => existsSync(target) && readFileSync(target).equals(readFileSync(icon)),
    copyIcon: () => copyFileSync(icon, target),
    running: () => {
      try {
        return run('/usr/bin/pgrep', ['-f', path.join(app, 'Contents', 'MacOS', 'Electron')]).trim() !== ''
      } catch {
        return false                                              // pgrep exits with 1 when it finds nothing
      }
    },
    sign: () => { run('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', app]) },
  }
}

function main() {
  if (process.platform !== 'darwin') return
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
  const app = path.join(root, 'node_modules', 'electron', 'dist', 'Electron.app')
  const icon = path.join(root, 'icons', 'icon.icns')
  if (!existsSync(app) || !existsSync(icon)) return
  const result = brand(realOps(app, icon))
  if (result.changed) console.log(`Gave the development copy of Electron the name and icon of ${NAME}.`)
  else if (result.skipped) console.warn(result.skipped)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main()
