import { useCallback, useEffect, useRef, useState } from 'react'
import type { createApi } from '../api/client'
import type { ApiError, UsageLimits, UsageReport } from '../api/types'
import { refreshDelayMs } from '../lib/usage'

type Api = ReturnType<typeof createApi>

/**
 * The usage report (what the model has been used for, the limits, the closest one). It is read when the app is ready, again after every answer (`refresh`), and on a timer:
 * every 5 seconds while a limit per minute is the closest one, otherwise every minute. Saving limits and resetting the counts answer with the new report.
 */
export function useUsage(api: Api, ready: boolean) {
  const [report, setReport] = useState<UsageReport | null>(null)
  const [problem, setProblem] = useState<ApiError | null>(null)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])

  const take = useCallback((result: Awaited<ReturnType<Api['getUsage']>>) => {
    if (!alive.current) return null
    if (result.ok) {
      setReport(result.data)
      setProblem(null)
      return null
    }
    setProblem(result.error)
    return result.error
  }, [])

  const refresh = useCallback(async () => { take(await api.getUsage()) }, [api, take])

  useEffect(() => {
    if (!ready) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const loop = async (delay: number) => {
      timer = setTimeout(async () => {
        const result = await api.getUsage()
        if (stopped) return
        take(result)
        void loop(result.ok ? refreshDelayMs(result.data) : 60000)
      }, delay)
    }
    void (async () => {
      const result = await api.getUsage()
      if (stopped) return
      take(result)
      void loop(result.ok ? refreshDelayMs(result.data) : 60000)
    })()
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
    }
  }, [api, ready, take])

  const saveLimits = useCallback(async (limits: UsageLimits | null): Promise<ApiError | null> => take(await api.saveUsageLimits(limits)), [api, take])
  const reset = useCallback(async (): Promise<ApiError | null> => take(await api.resetUsage()), [api, take])

  return { report, problem, refresh, saveLimits, reset }
}
