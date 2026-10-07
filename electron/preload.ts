import { ipcRenderer, contextBridge } from 'electron'
import { CHANNELS } from './backend/channels.ts'

// --------- Expose some API to the Renderer process ---------
contextBridge.exposeInMainWorld('ipcRenderer', {
  on(...args: Parameters<typeof ipcRenderer.on>) {
    const [channel, listener] = args
    return ipcRenderer.on(channel, (event, ...args) => listener(event, ...args))
  },
  off(...args: Parameters<typeof ipcRenderer.off>) {
    const [channel, ...omit] = args
    return ipcRenderer.off(channel, ...omit)
  },
  send(...args: Parameters<typeof ipcRenderer.send>) {
    const [channel, ...omit] = args
    return ipcRenderer.send(channel, ...omit)
  },
  invoke(...args: Parameters<typeof ipcRenderer.invoke>) {
    const [channel, ...omit] = args
    return ipcRenderer.invoke(channel, ...omit)
  },

  // You can expose other APTs you need here.
  // ...
})

// The ONLY way the window talks to the Python backend (task 8.7). The token and the port stay in the main process; the window names a method, a path
// and a JSON body, and the main process checks them (backend/ipc.ts, backend/request.ts).
contextBridge.exposeInMainWorld('clankBackend', {
  request: (method: string, path: string, body?: object) => ipcRenderer.invoke(CHANNELS.request, { method, path, body }),
  status: () => ipcRenderer.invoke(CHANNELS.status),
  restart: () => ipcRenderer.invoke(CHANNELS.restart),
  pickFolder: () => ipcRenderer.invoke(CHANNELS.pickFolder),
  // The only door for an API key: the main process encrypts it, stores it and pushes it to the backend. There is no way to read a key back.
  saveLlmSettings: (config: object, apiKey?: string | null) => ipcRenderer.invoke(CHANNELS.llmSave, apiKey === undefined ? { config } : { config, apiKey }),
  // Answers written live. The window picks the id (so it can stop an answer even while the backend is still searching), the main process makes the request and pushes
  // the events back to THIS window only. Nothing here carries the token or the port.
  startStream: (id: string, path: string, body?: object) => ipcRenderer.invoke(CHANNELS.streamStart, body === undefined ? { id, path } : { id, path, body }),
  stopStream: (id: string) => ipcRenderer.invoke(CHANNELS.streamStop, { id }),
  onStreamEvents(listener: (message: unknown) => void) {
    const handler = (_event: unknown, message: unknown) => listener(message)
    ipcRenderer.on(CHANNELS.streamEvents, handler)
    return () => { ipcRenderer.off(CHANNELS.streamEvents, handler) }
  },
  onStatus(listener: (status: unknown) => void) {
    const handler = (_event: unknown, status: unknown) => listener(status)
    ipcRenderer.on(CHANNELS.statusChanged, handler)
    return () => { ipcRenderer.off(CHANNELS.statusChanged, handler) }
  },
})
