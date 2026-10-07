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
    /** Saves the answer-model choice and, if given, the key (a string stores it, null removes it, undefined leaves it). The key can be written, never read back. */
    saveLlmSettings(config: object, apiKey?: string | null): Promise<{ ok: boolean, status: number, body: unknown }>
    /** Starts an answer written live. `id` is chosen by the caller (8 to 64 letters, digits, _ or -). Resolves when the backend has answered the request: {ok: true, body: {id}} and then
     *  events arrive through onStreamEvents, or {ok: false, ...} for a refusal (nothing will arrive). */
    startStream(id: string, path: string, body?: object): Promise<{ ok: boolean, status: number, body: unknown }>
    /** Stops an answer this window started. Resolves with {body: {stopped}}; the window then also gets a `cancelled` event. */
    stopStream(id: string): Promise<{ ok: boolean, status: number, body: unknown }>
    /** Events of this window's streams: `{id, events}` in batches, the last event of a stream being done, error or cancelled. */
    onStreamEvents(listener: (message: unknown) => void): () => void
    /** True when the window is full screen (the Mac's traffic lights are hidden then), false when it is not. Also told once when the page has loaded. */
    onFullscreen(listener: (full: boolean) => void): () => void
    /** Tells the main process which theme the window shows (it sets the app's native theme, which the Mac's see-through sidebar follows). */
    setTheme(theme: 'light' | 'dark' | 'system'): void
    onStatus(listener: (status: import('./backend/service.ts').BackendStatus) => void): () => void
  }
}
