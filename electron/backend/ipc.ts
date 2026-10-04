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

const FORBIDDEN = { ok: false, status: 0, body: { error: { code: 'forbidden', message: 'This page may not use the Clank backend.' } } }
const BAD_REQUEST = { ok: false, status: 0, body: { error: { code: 'bad_request', message: 'The request was not allowed.' } } }

export function registerBackendIpc({ ipcMain, service, isTrusted }: { ipcMain: IpcMainLike, service: ServiceLike, isTrusted: (url: string | undefined) => boolean }): void {
  const trusted = (event: unknown) => isTrusted((event as { senderFrame?: { url?: string } | null })?.senderFrame?.url)

  ipcMain.handle(CHANNELS.request, (async (event: unknown, payload: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    const p = payload as { method?: unknown, path?: unknown, body?: unknown } | null | undefined
    if (typeof p !== 'object' || p === null || Array.isArray(p) || typeof p.method !== 'string' || typeof p.path !== 'string') return BAD_REQUEST
    return service.request(p.method, p.path, p.body)           // only these three fields: an address, a port or a header from the window is never passed on
  }) as never)

  ipcMain.handle(CHANNELS.status, ((event: unknown) => (trusted(event) ? service.status() : FORBIDDEN)) as never)

  ipcMain.handle(CHANNELS.restart, (async (event: unknown) => {
    if (!trusted(event)) return FORBIDDEN
    await service.restart()
    return service.status()
  }) as never)
}
