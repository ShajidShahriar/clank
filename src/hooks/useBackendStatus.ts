import { useEffect, useState } from 'react'
import { getBackendControl } from '../bridge'
import type { BackendView } from '../types'

/** Where the Python backend is in its life (starting, ready, crashed...), pushed by the desktop app. */
export function useBackendStatus(): BackendView {
  const [view, setView] = useState<BackendView>(() =>
    getBackendControl()
      ? { state: 'starting', message: 'Starting the Clank backend.', detail: [] }
      : { state: 'no_bridge', message: 'Clank can only reach its backend from the desktop app.', detail: [] },
  )

  useEffect(() => {
    const control = getBackendControl()
    if (!control) return
    let alive = true
    control.status().then((status) => { if (alive && status && typeof status.state === 'string') setView(status as BackendView) }).catch(() => {})
    const off = control.onStatus((status) => setView(status as BackendView))
    return () => {
      alive = false
      off()
    }
  }, [])

  return view
}
