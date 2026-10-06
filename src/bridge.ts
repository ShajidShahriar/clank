import type { Bridge } from './api/client'

/** The bridge the desktop app's preload script gives the page. It does not exist when the page is opened in a plain browser. */
export function getBridge(): Bridge | undefined {
  return (window as unknown as { clankBackend?: Bridge }).clankBackend
}

export function getBackendControl(): Window['clankBackend'] | undefined {
  return (window as unknown as { clankBackend?: Window['clankBackend'] }).clankBackend
}
