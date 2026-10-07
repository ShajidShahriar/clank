// The names of the IPC channels (task 8.7). Its own file with no imports, so the preload script can use it without pulling in anything from Node.
export const CHANNELS = {
  request: 'clank:backend-request',
  status: 'clank:backend-status',
  restart: 'clank:backend-restart',
  pickFolder: 'clank:pick-folder',
  llmSave: 'clank:llm-save',
  streamStart: 'clank:stream-start',
  streamStop: 'clank:stream-stop',
  streamEvents: 'clank:stream-events',           // pushed by the main process to the window that asked, not handled
  fullscreen: 'clank:fullscreen',                     // pushed by the main process: true when the window goes full screen (the traffic lights hide), false when it leaves; not handled
  theme: 'clank:theme',                               // sent by the window (light, dark or system), no reply; the main process checks who sent it and what it says
  statusChanged: 'clank:backend-status-changed',      // pushed by the main process, not handled
} as const
