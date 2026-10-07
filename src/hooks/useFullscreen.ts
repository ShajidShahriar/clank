import { useEffect } from 'react'
import { getBackendControl } from '../bridge'

/**
 * Puts the class `fs` on the page while the window is full screen. On a Mac the traffic lights are hidden then, so the things that made
 * room for them (the fold button, the top bar) use that room again. The main process says when the window enters and leaves full screen
 * (window.clankBackend.onFullscreen): comparing sizes does not work, because a Mac with a notch leaves a full-screen window shorter than the screen.
 */
export function useFullscreen(): void {
  useEffect(() => {
    const root = document.documentElement
    const off = getBackendControl()?.onFullscreen((full) => root.classList.toggle('fs', full))
    return () => {
      off?.()
      root.classList.remove('fs')
    }
  }, [])
}
