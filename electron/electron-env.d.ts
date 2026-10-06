/// <reference types="vite-plugin-electron/electron-env" />

declare namespace NodeJS {
  interface ProcessEnv {
    /**
     * The built directory structure
     *
     * ```tree
     * ├─┬─┬ dist
     * │ │ └── index.html
     * │ │
     * │ ├─┬ dist-electron
     * │ │ ├── main.js
     * │ │ └── preload.js
     * │
     * ```
     */
    APP_ROOT: string
    /** /dist/ or /public/ */
    VITE_PUBLIC: string
  }
}

// Used in Renderer process, expose in `preload.ts`
interface Window {
  ipcRenderer: import('electron').IpcRenderer
  /** The only way to reach the Python backend (see electron/backend/). `ok` is false for any status outside 200-299 and for every failure to reach it (status 0). */
  clankBackend: {
    request(method: 'GET' | 'POST' | 'DELETE', path: string, body?: object): Promise<{ ok: boolean, status: number, body: unknown }>
    status(): Promise<import('./backend/service.ts').BackendStatus>
    restart(): Promise<import('./backend/service.ts').BackendStatus>
    /** Opens the system folder dialog. `body.path` is the chosen folder, or null if the dialog was cancelled. */
    pickFolder(): Promise<{ ok: boolean, status: number, body: unknown }>
    onStatus(listener: (status: import('./backend/service.ts').BackendStatus) => void): () => void
  }
}
