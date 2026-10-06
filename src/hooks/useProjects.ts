import { useCallback, useEffect, useMemo, useState } from 'react'
import type { createApi } from '../api/client'
import type { ApiError, IndexStatus, Project } from '../api/types'

type Api = ReturnType<typeof createApi>
const POLL_MS = 1000
const BUSY = ['running', 'cancelling']

/** The projects, which one is selected, and the live index status of the ones being indexed (polled once a second while any is). */
export function useProjects(api: Api, ready: boolean) {
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [live, setLive] = useState<Record<number, IndexStatus>>({})
  const [problem, setProblem] = useState<ApiError | null>(null)

  const refresh = useCallback(async () => {
    const result = await api.listProjects()
    if (!result.ok) {
      setProblem(result.error)
      return
    }
    setProblem(null)
    setProjects(result.data)
    setSelectedId((current) => (current !== null && result.data.some((p) => p.id === current) ? current : (result.data[0]?.id ?? null)))
  }, [api])

  useEffect(() => {
    if (ready) void refresh()
  }, [ready, refresh])

  const busyIds = useMemo(
    () => projects.filter((p) => BUSY.includes(live[p.id]?.state ?? p.index_state)).map((p) => p.id),
    [projects, live],
  )
  const busyKey = busyIds.join(',')

  useEffect(() => {
    if (!busyKey) return
    const ids = busyKey.split(',').map(Number)
    const timer = setInterval(async () => {
      let finished = false
      for (const id of ids) {
        const result = await api.indexStatus(id)
        if (!result.ok) continue
        setLive((previous) => ({ ...previous, [id]: result.data }))
        if (!BUSY.includes(result.data.state)) finished = true
      }
      if (finished) void refresh()
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [busyKey, api, refresh])

  const startIndex = useCallback(async (id: number) => {
    const result = await api.startIndex(id)
    if (result.ok) setLive((previous) => ({ ...previous, [id]: result.data }))
    else setProblem(result.error)
    await refresh()
  }, [api, refresh])

  const cancelIndex = useCallback(async (id: number) => {
    const result = await api.cancelIndex(id)
    if (result.ok) setLive((previous) => ({ ...previous, [id]: result.data }))
    else setProblem(result.error)
  }, [api])

  /** Choose a folder, add it as a project and start indexing it. Returns an error to show, or null. */
  const addProject = useCallback(async (): Promise<ApiError | null> => {
    const picked = await api.pickFolder()
    if (!picked.ok) return picked.error
    if (picked.data === null) return null                       // the dialog was cancelled
    const added = await api.addProject(picked.data)
    if (!added.ok) return added.error
    await refresh()
    setSelectedId(added.data.id)
    await startIndex(added.data.id)
    return null
  }, [api, refresh, startIndex])

  const removeProject = useCallback(async (id: number): Promise<ApiError | null> => {
    const result = await api.deleteProject(id)
    if (!result.ok) return result.error
    setLive((previous) => {
      const next = { ...previous }
      delete next[id]
      return next
    })
    await refresh()
    return null
  }, [api, refresh])

  return { projects, selectedId, setSelectedId, live, problem, refresh, startIndex, cancelIndex, addProject, removeProject }
}
