// The names of the IPC channels (task 8.7). Its own file with no imports, so the preload script can use it without pulling in anything from Node.
export const CHANNELS = {
  request: 'clank:backend-request',
  status: 'clank:backend-status',
  restart: 'clank:backend-restart',
  pickFolder: 'clank:pick-folder',
  llmSave: 'clank:llm-save',
  statusChanged: 'clank:backend-status-changed',      // pushed by the main process, not handled
} as const
