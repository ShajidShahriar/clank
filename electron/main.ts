import { app, BrowserWindow, ipcMain } from 'electron'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { CHANNELS, isTrustedSender, registerBackendIpc } from './backend/ipc.ts'
import { preloadFile } from './backend/preload-path.ts'
import { BackendService } from './backend/service.ts'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// The built directory structure
//
// ├─┬─┬ dist
// │ │ └── index.html
// │ │
// │ ├─┬ dist-electron
// │ │ ├── main.js
// │ │ └── preload.mjs
// │
process.env.APP_ROOT = path.join(__dirname, '..')

// 🚧 Use ['ENV_NAME'] avoid vite:define plugin - Vite@2.x
export const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL']
export const MAIN_DIST = path.join(process.env.APP_ROOT, 'dist-electron')
export const RENDERER_DIST = path.join(process.env.APP_ROOT, 'dist')

process.env.VITE_PUBLIC = VITE_DEV_SERVER_URL ? path.join(process.env.APP_ROOT, 'public') : RENDERER_DIST

let win: BrowserWindow | null
let backend: BackendService | null = null

// Only the app's own page may use the backend bridge (and only the app's own page may be shown in the window).
const trustContext = () => ({ devServerUrl: VITE_DEV_SERVER_URL, rendererIndexPath: path.join(RENDERER_DIST, 'index.html') })

function createWindow() {
  win = new BrowserWindow({
    title: 'Clank',
    icon: path.join(process.env.VITE_PUBLIC, 'logo_dark.png'),
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: '#000000',
    show: false,
    webPreferences: {
      // the build writes preload.js or preload.mjs depending on the package's module type: find the one that exists
      preload: preloadFile(__dirname, { exists: fs.existsSync, modified: (p) => fs.statSync(p).mtimeMs }),
    },
  })

  win.once('ready-to-show', () => {
    win?.show()
  })

  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.webContents.on('will-navigate', (event, url) => {
    if (!isTrustedSender(url, trustContext())) event.preventDefault()
  })

  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL)
  } else {
    win.loadFile(path.join(RENDERER_DIST, 'index.html'))
  }
}

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
    win = null
  }
})

app.on('activate', () => {
  // On OS X it's common to re-create a window in the app when the
  // dock icon is clicked and there are no other windows open.
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})

// The backend is started here and stopped when the app quits. The window reaches it only through the IPC bridge (backend/ipc.ts): it never sees the token.
function startBackend() {
  backend = new BackendService({
    userDataPath: app.getPath('userData'),
    appRoot: process.env.APP_ROOT,
    resourcesPath: process.resourcesPath,
    packaged: app.isPackaged,
    platform: process.platform,
    baseEnv: process.env,
    spawn: spawn as never,
  })
  registerBackendIpc({ ipcMain, service: backend, isTrusted: (url) => isTrustedSender(url, trustContext()) })
  backend.onStatus((status) => {
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send(CHANNELS.statusChanged, status)
  })
  void backend.start()          // not awaited: the window opens at once and shows "starting" until the backend is ready
}

let quitting = false
app.on('before-quit', (event) => {
  if (quitting || backend === null) return
  quitting = true
  event.preventDefault()        // wait for the backend to stop (at most its kill grace period), then really quit
  backend.stop().catch(() => {}).finally(() => app.quit())
})
process.on('exit', () => backend?.killNow())      // the last line of defence: a synchronous SIGKILL

app.whenReady().then(() => {
  startBackend()
  createWindow()
})
