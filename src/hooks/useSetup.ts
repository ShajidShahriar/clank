import { useCallback, useEffect, useState } from 'react'
import type { createApi } from '../api/client'
import type { Health, PullStatus } from '../api/types'
import { setupView } from '../lib/setup'

type Api = ReturnType<typeof createApi>

/**
 * First run: is the search model ready, and if not, why. Asks the backend's health (and the state of the model download) every couple of seconds
 * while the model is not ready, and stops asking once it is. `startPull` downloads the model through Ollama.
 */
export function useSetup(api: Api, backendReady: boolean) {
  const [health, setHealth] = useState<Health | null>(null)
  const [pull, setPull] = useState<PullStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const result = await api.health()
    if (!result.ok) return
    setHealth(result.data)
    if (result.data.embedder === 'ready') return
    const status = await api.getPullStatus()
    if (status.ok) setPull(status.data)                                        // a setup without Ollama answers 409: no download, no status
  }, [api])

  useEffect(() => {
    if (!backendReady) {
      setHealth(null)
      setPull(null)
      return
    }
    void refresh()
  }, [backendReady, refresh])

  const needsWatching = backendReady && health?.embedder !== 'ready'
  const pulling = pull?.state === 'pulling'
  useEffect(() => {
    if (!needsWatching) return
    const timer = setInterval(() => { void refresh() }, pulling ? 1000 : 2500)
    return () => clearInterval(timer)
  }, [needsWatching, pulling, refresh])

  const startPull = useCallback(async () => {
    setStarting(true)
    setActionError(null)
    const result = await api.startPull()
    setStarting(false)
    if (result.ok) setPull(result.data)
    else setActionError(result.error.message)
    void refresh()
  }, [api, refresh])

  const checkAgain = useCallback(() => {
    setActionError(null)
    void refresh()
  }, [refresh])

  return { view: backendReady ? setupView(health, pull) : { kind: 'none' as const }, starting, actionError, startPull, checkAgain }
}
