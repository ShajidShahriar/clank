// The bridge between the window and the backend (task 8.7). The window never sees the token: it asks the main process, which checks WHO is asking (only the
// app's own page), checks WHAT is asked (a method, a path and a JSON body, nothing else is passed on) and calls the backend itself.
import { pathToFileURL } from 'node:url'
import { CHANNELS } from './channels.ts'

export { CHANNELS }

export type TrustContext = {
  devServerUrl: string | undefined          // set in development: the Vite dev server
  rendererIndexPath: string                 // the packaged page: <app>/dist/index.html
}

/** Is this page the app's own? In development only the dev server's origin; packaged, only the app's own index.html (a hash or query is fine). */
export function isTrustedSender(url: string | undefined, { devServerUrl, rendererIndexPath }: TrustContext): boolean {
  if (typeof url !== 'string' || url === '') return false
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (devServerUrl) {
    try {
      return parsed.origin === new URL(devServerUrl).origin      // a file: page has the origin "null", so it can never match
    } catch {
      return false
    }
  }
  const own = pathToFileURL(rendererIndexPath)
  return parsed.protocol === 'file:' && parsed.host === '' && parsed.pathname === own.pathname
}

// The real ipcMain hands its listener an IpcMainInvokeEvent; this file only reads `senderFrame.url` from it, defensively, so the type is kept loose.
type IpcMainLike = { handle(channel: string, listener: (event: any, ...args: any[]) => unknown): void }
type ServiceLike = {
  request(method: string, path: string, body?: unknown): Promise<unknown>
  status(): unknown
  restart(): Promise<void>
}

type StreamsLike = {
  start(owner: { id: number, send(message: unknown): void }, request: { id: string, path: string, body?: unknown }): Promise<unknown>
  stop(ownerId: number, id: string): boolean
  closeOwner(ownerId: number): void
}

const WINDOW_METHODS = ['GET', 'POST', 'DELETE']
const isPlain = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const STREAM_FAILED = { ok: false, status: 0, body: { error: { code: 'stream_failed', message: 'The answer could not be started.' } } }
const FORBIDDEN = { ok: false, status: 0, body: { error: { code: 'forbidden', message: 'This page may not use the Clank backend.' } } }
const BAD_REQUEST = { ok: false, status: 0, body: { error: { code: 'bad_request', message: 'The request was not allowed.' } } }

export function registerBackendIpc({ ipcMain, service, isTrusted, pickFolder, saveLlm, streams }: {
  ipcMain: IpcMainLike
  service: ServiceLike
  isTrusted: (url: string | undefined) => boolean
  pickFolder: () => Promise<string | null>          // opens the system's folder dialog; null when it was cancelled
  saveLlm: (payload: { config: object, apiKey?: string | null }) => Promise<unknown>      // the settings flow (settings-flow.ts): the key goes through here and nowhere else
  streams: StreamsLike                                                                  // answers written live (streams.ts)
}): void {
  const trusted = (event: unknown) => isTrusted((event as { senderFrame?: { url?: string } | null })?.senderFrame?.url)

  // The window that asked is the one that gets the events, and the only one that can stop them. It is taken from the event (the sender), never from the payload. A window
  // whose page reloads or navigates away, whose renderer crashes, or that is destroyed, loses its streams at once: nobody could read the answer, and the model would go
  // on being paid for it.
  const watched = new Set<number>()
  const ownerOf = (event: unknown) => {
    const sender = (event as { sender?: any } | null | undefined)?.sender
    if (!sender || typeof sender.id !== 'number' || typeof sender.send !== 'function' || typeof sender.isDestroyed !== 'function') return null
    if (!watched.has(sender.id)) {
      const id = sender.id
      watched.add(id)
      sender.once?.('destroyed', () => { watched.delete(id); streams.closeOwner(id) })
      sender.on?.('did-start-navigation', (details: { isMainFrame?: boolean, isSameDocument?: boolean } | undefined) => { if (details?.isMainFrame && !details.isSameDocument) streams.closeOwner(id) })
      sender.on?.('render-process-gone', () => streams.closeOwner(id))
    }
    return { id: sender.id as number, send: (message: unknown) => { if (sender.isDestroyed()) throw new Error('the window is gone'); sender.send(CHANNELS.streamEvents, message) } }
  }

  ipcMain.handle(CHANNELS.request, (async (event: unknown, payload: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    const p = payload as { method?: unknown, path?: unknown, body?: unknown } | null | undefined
    if (typeof p !== 'object' || p === null || Array.isArray(p) || typeof p.method !== 'string' || typeof p.path !== 'string') return BAD_REQUEST
    if (!WINDOW_METHODS.includes(p.method)) return BAD_REQUEST          // a PUT is the main process's own: the key goes through the settings flow, never this door
    return service.request(p.method, p.path, p.body)           // only these three fields: an address, a port or a header from the window is never passed on
  }) as never)

  ipcMain.handle(CHANNELS.streamStart, (async (event: unknown, payload: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    const owner = ownerOf(event)
    if (owner === null || !isPlain(payload) || typeof payload.id !== 'string' || typeof payload.path !== 'string' || (payload.body !== undefined && !isPlain(payload.body))) return BAD_REQUEST
    try {
      return await streams.start(owner, { id: payload.id, path: payload.path, body: payload.body })       // only these three fields: an address, a port or an owner from the window is never passed on
    } catch {
      return STREAM_FAILED
    }
  }) as never)

  ipcMain.handle(CHANNELS.streamStop, ((event: unknown, payload: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    const sender = (event as { sender?: { id?: unknown } } | null | undefined)?.sender
    if (typeof sender?.id !== 'number' || !isPlain(payload) || typeof payload.id !== 'string') return BAD_REQUEST
    return { ok: true, status: 200, body: { stopped: streams.stop(sender.id, payload.id) } }
  }) as never)

  ipcMain.handle(CHANNELS.status, ((event: unknown) => (trusted(event) ? service.status() : FORBIDDEN)) as never)

  ipcMain.handle(CHANNELS.restart, (async (event: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    await service.restart()
    return service.status()
  }) as never)

  // The window adds a project by choosing its folder. Only the app's own page may open the dialog, and only a real path (or null) goes back.
  ipcMain.handle(CHANNELS.pickFolder, (async (event: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    try {
      const chosen = await pickFolder()
      return { ok: true, status: 200, body: { path: typeof chosen === 'string' && chosen !== '' ? chosen : null } }
    } catch {
      return { ok: false, status: 0, body: { error: { code: 'dialog_failed', message: 'The folder dialog could not be opened.' } } }
    }
  }) as never)

  // Saving the answer-model settings. This is the ONLY door for a key: it goes to the main process, which encrypts it and pushes it to the backend's memory.
  // Only `config` and `apiKey` are passed on (a missing apiKey stays missing: it means "leave the stored key alone").
  ipcMain.handle(CHANNELS.llmSave, (async (event: unknown, payload: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    const p = payload as { config?: unknown, apiKey?: unknown } | null | undefined
    if (typeof p !== 'object' || p === null || Array.isArray(p) || typeof p.config !== 'object' || p.config === null || Array.isArray(p.config)) return BAD_REQUEST
    const clean: { config: object, apiKey?: string | null } = { config: p.config }
    if ('apiKey' in p && p.apiKey !== undefined) clean.apiKey = p.apiKey as string | null
    try {
      return await saveLlm(clean)
    } catch {
      return { ok: false, status: 0, body: { error: { code: 'settings_failed', message: 'The settings could not be saved.' } } }
    }
  }) as never)
}
