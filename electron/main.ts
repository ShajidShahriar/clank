import { app, BrowserWindow, dialog, ipcMain, nativeTheme, safeStorage } from 'electron'
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { CHANNELS, isTrustedSender, registerBackendIpc } from './backend/ipc.ts'
import { preloadFile } from './backend/preload-path.ts'
import { createKeyStore } from './backend/secrets.ts'
import { createSettingsFlow } from './backend/settings-flow.ts'
import { BackendService } from './backend/service.ts'
import { StreamManager } from './backend/streams.ts'
import { themeSource } from './backend/theme.ts'

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
let streams: StreamManager | null = null

// Only the app's own page may use the backend bridge (and only the app's own page may be shown in the window).
const trustContext = () => ({ devServerUrl: VITE_DEV_SERVER_URL, rendererIndexPath: path.join(RENDERER_DIST, 'index.html') })

function createWindow() {
  win = new BrowserWindow({
    title: 'Clank',
    icon: path.join(process.env.VITE_PUBLIC, 'icon.png'),
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    // On a Mac: no title bar (the traffic lights sit inside the window), and the sidebar is see-through (vibrancy). Elsewhere: a plain window.
    ...(process.platform === 'darwin'
      ? { titleBarStyle: 'hiddenInset' as const, trafficLightPosition: { x: 18, y: 18 }, vibrancy: 'sidebar' as const, visualEffectState: 'followWindow' as const, backgroundColor: '#00000000' }
      : { backgroundColor: '#000000' }),
    show: false,
    webPreferences: {
      // the build writes preload.js or preload.mjs depending on the package's module type: find the one that exists
      preload: preloadFile(__dirname, { exists: fs.existsSync, modified: (p) => fs.statSync(p).mtimeMs }),
    },
  })

  // Tell the page when it is full screen, so it can use the room the traffic lights leave. It also asks once when it has loaded.
  const tellFullscreen = () => { if (win && !win.isDestroyed()) win.webContents.send(CHANNELS.fullscreen, win.isFullScreen()) }
  win.on('enter-full-screen', tellFullscreen)
  win.on('leave-full-screen', tellFullscreen)
  win.webContents.on('did-finish-load', tellFullscreen)

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
  // The answer-model key: encrypted with the system keychain, kept in the user-data folder, pushed to the backend's memory after every start. Never written in plain text.
  const keys = createKeyStore({ dir: path.join(app.getPath('userData'), 'secrets'), safeStorage })
  const settings = createSettingsFlow({ service: backend, keys })
  const running = backend
  streams = new StreamManager({ target: () => running.target() })           // answers written live: the main process makes the request, so the window never sees the token
  ipcMain.on(CHANNELS.theme, (event, value) => {
    if (!isTrustedSender(event.senderFrame?.url, trustContext())) return
    nativeTheme.themeSource = themeSource(value)
  })

  registerBackendIpc({
    ipcMain, service: backend, isTrusted: (url) => isTrustedSender(url, trustContext()), streams,
    saveLlm: (payload) => settings.save(payload as never),
    pickFolder: async () => {
      const options = { title: 'Choose the project folder', properties: ['openDirectory' as const] }
      const result = win ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options)
      return result.canceled ? null : (result.filePaths[0] ?? null)
    },
  })
  backend.onStatus((status) => {
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send(CHANNELS.statusChanged, status)
    if (status.state === 'ready') void settings.onBackendReady()        // the backend's memory is empty after a start: push the key of the active provider
    else streams?.abortAll()                                            // stopped, crashed, restarting or failed: every answer being written ends with an error
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
  // A packaged Mac app gets its icon from the bundle (icons/icon.icns, set in electron-builder.json5). In development the Dock would show Electron's own, so set ours.
  if (process.platform === 'darwin' && VITE_DEV_SERVER_URL) app.dock?.setIcon(path.join(process.env.VITE_PUBLIC!, 'icon.png'))
  startBackend()
  createWindow()
})
