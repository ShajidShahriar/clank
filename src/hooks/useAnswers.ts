import { useCallback, useRef, useState } from 'react'
import type { createApi } from '../api/client'
import type { Entry } from '../types'

type Api = ReturnType<typeof createApi>

/** The questions asked in each project and their answers. Kept in memory for now: closing the app forgets them. */
export function useAnswers(api: Api) {
  const [byProject, setByProject] = useState<Record<number, Entry[]>>({})
  const counter = useRef(0)

  const patch = useCallback((projectId: number, id: string, change: Partial<Entry>) => {
    setByProject((previous) => ({
      ...previous,
      [projectId]: (previous[projectId] ?? []).map((entry) => (entry.id === id ? { ...entry, ...change } : entry)),
    }))
  }, [])

  const run = useCallback(async (projectId: number, id: string, question: string, allowRemote: boolean) => {
    patch(projectId, id, { state: 'pending', error: undefined })
    const result = await api.ask(projectId, question, allowRemote)
    if (result.ok) patch(projectId, id, { state: 'done', answer: result.data, error: undefined })
    else patch(projectId, id, { state: 'error', error: result.error, answer: undefined })
  }, [api, patch])

  const ask = useCallback((projectId: number, question: string, allowRemote: boolean) => {
    counter.current += 1
    const id = `q${counter.current}`
    setByProject((previous) => ({ ...previous, [projectId]: [...(previous[projectId] ?? []), { id, question, state: 'pending' }] }))
    void run(projectId, id, question, allowRemote)
  }, [run])

  const retry = useCallback((projectId: number, entry: Entry, allowRemote: boolean) => {
    void run(projectId, entry.id, entry.question, allowRemote)
  }, [run])

  const forget = useCallback((projectId: number) => {
    setByProject((previous) => {
      const next = { ...previous }
      delete next[projectId]
      return next
    })
  }, [])

  return { byProject, ask, retry, forget }
}
