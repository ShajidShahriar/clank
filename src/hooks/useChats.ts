import { useCallback, useEffect, useRef, useState } from 'react'
import type { createApi } from '../api/client'
import type { ApiError, ConversationSummary, StreamHandle } from '../api/types'
import { entriesFromMessages, entriesKey } from '../lib/conversation'
import { entryAfterEvent, startEntry } from '../lib/liveAnswer'
import type { Entry } from '../types'

type Api = ReturnType<typeof createApi>

/**
 * The saved conversations of each project, which one is open, and the entries (questions with their answers) of each.
 * The open conversation of a project is remembered while the window runs; a project the window has not shown yet starts in a new chat (no conversation open), and a
 * new question in "no conversation yet" creates one first.
 */
export function useChats(api: Api, ready: boolean, projectId: number | null) {
  const [lists, setLists] = useState<Record<number, ConversationSummary[]>>({})
  const [active, setActive] = useState<Record<number, number | null>>({})
  const [entries, setEntries] = useState<Record<string, Entry[]>>({})
  const [problem, setProblem] = useState<ApiError | null>(null)
  const counter = useRef(0)
  const handles = useRef(new Map<string, StreamHandle>())               // the answers being written live, by `<entries key>|<entry id>`
  const activeRef = useRef(active)
  activeRef.current = active
  const entriesRef = useRef(entries)
  entriesRef.current = entries

  const setOpen = useCallback((pid: number, cid: number | null) => {
    activeRef.current = { ...activeRef.current, [pid]: cid }
    setActive(activeRef.current)
  }, [])

  const update = useCallback((key: string, id: string, change: (entry: Entry) => Entry) => {
    setEntries((previous) => {
      const list = previous[key]
      if (!list || !list.some((entry) => entry.id === id)) return previous            // the conversation was deleted meanwhile: nothing to update
      return { ...previous, [key]: list.map((entry) => (entry.id === id ? change(entry) : entry)) }       // (the other entries keep their identity: they are not drawn again)
    })
  }, [])

  const patch = useCallback((key: string, id: string, change: Partial<Entry>) => update(key, id, (entry) => ({ ...entry, ...change })), [update])

  const loadList = useCallback(async (pid: number) => {
    const result = await api.listConversations(pid)
    if (!result.ok) {
      setProblem(result.error)
      return null
    }
    setLists((previous) => ({ ...previous, [pid]: result.data }))
    return result.data
  }, [api])

  const open = useCallback(async (pid: number, cid: number) => {
    setOpen(pid, cid)
    const key = entriesKey(pid, cid)
    if (entriesRef.current[key]) return                                                // already here (maybe with an answer still pending): keep it
    const result = await api.getConversation(pid, cid)
    if (!result.ok) {
      setProblem(result.error)
      return
    }
    setEntries((previous) => (previous[key] ? previous : { ...previous, [key]: entriesFromMessages(result.data.messages) }))
  }, [api, setOpen])

  useEffect(() => {
    if (!ready || projectId === null) return
    void (async () => {
      await loadList(projectId)                                                        // the list only: the app always starts in a new chat, never in an old one
    })()
  }, [ready, projectId, loadList])

  const run = useCallback(async (pid: number, key: string, cid: number, id: string, question: string, allowRemote: boolean) => {
    if (!api.canStream) {                                                              // an older desktop app: ask for the whole answer at once
      update(key, id, () => startEntry(id, question, Date.now()))
      const result = await api.ask(pid, question, allowRemote, undefined, cid)
      if (result.ok) {
        update(key, id, () => ({ id, question, state: 'done', answer: result.data }))
        void loadList(pid)                                                             // the title and the order changed
      } else {
        update(key, id, () => ({ id, question, state: 'error', error: result.error }))
      }
      return
    }
    update(key, id, () => startEntry(id, question, Date.now()))
    const handle = api.askStream({
      projectId: pid, question, allowRemote, conversationId: cid,
      onEvent: (event) => {
        update(key, id, (entry) => entryAfterEvent(entry, event, Date.now()))
        if (event.type === 'done') void loadList(pid)                                  // the title and the order changed
      },
    })
    const handleKey = `${key}|${id}`
    handles.current.set(handleKey, handle)
    const end = await handle.finished
    if (handles.current.get(handleKey) === handle) handles.current.delete(handleKey)
    if (!end.ok) {                                                                     // refused, or stopped before the backend had answered: no event will come
      update(key, id, () => (end.error.code === 'cancelled' ? { id, question, state: 'stopped' } : { id, question, state: 'error', error: end.error }))
    }
  }, [api, update, loadList])

  const ask = useCallback(async (pid: number, question: string, allowRemote: boolean) => {
    counter.current += 1
    const id = `q${counter.current}`
    let cid = activeRef.current[pid] ?? null
    const entry: Entry = startEntry(id, question, Date.now())
    if (cid !== null) {
      const key = entriesKey(pid, cid)
      setEntries((previous) => ({ ...previous, [key]: [...(previous[key] ?? []), entry] }))
      await run(pid, key, cid, id, question, allowRemote)
      return
    }
    const draft = entriesKey(pid, null)
    setEntries((previous) => ({ ...previous, [draft]: [...(previous[draft] ?? []), entry] }))
    const created = await api.createConversation(pid)
    if (!created.ok) {
      patch(draft, id, { state: 'error', error: created.error })
      return
    }
    cid = created.data.id
    const key = entriesKey(pid, cid)
    setEntries((previous) => {
      const { [draft]: moved = [], ...rest } = previous
      return { ...rest, [key]: moved }
    })
    setOpen(pid, cid)
    await run(pid, key, cid, id, question, allowRemote)
  }, [api, patch, run, setOpen])

  const retry = useCallback(async (pid: number, entry: Entry, allowRemote: boolean) => {
    const cid = activeRef.current[pid] ?? null
    if (cid === null) {
      const draft = entriesKey(pid, null)
      setEntries((previous) => ({ ...previous, [draft]: (previous[draft] ?? []).filter((e) => e.id !== entry.id) }))
      await ask(pid, entry.question, allowRemote)                                      // no conversation was ever created for it: start over
      return
    }
    await run(pid, entriesKey(pid, cid), cid, entry.id, entry.question, allowRemote)
  }, [ask, run])

  /** Stop the answers being written in the open conversation of a project. Each ends with a `cancelled` event (or, if the backend had not answered yet, with a stop). */
  const stop = useCallback((pid: number) => {
    const key = entriesKey(pid, activeRef.current[pid] ?? null)
    for (const entry of entriesRef.current[key] ?? []) {
      if (entry.state === 'pending' || entry.state === 'streaming') void handles.current.get(`${key}|${entry.id}`)?.stop()
    }
  }, [])

  const stopAllIn = useCallback((prefix: string) => {
    for (const [handleKey, handle] of handles.current) if (handleKey.startsWith(prefix)) void handle.stop()
  }, [])

  const select = useCallback((pid: number, cid: number) => { void open(pid, cid) }, [open])

  const startNew = useCallback((pid: number) => {
    setOpen(pid, null)
    setEntries((previous) => {
      const { [entriesKey(pid, null)]: _gone, ...rest } = previous
      return rest
    })
  }, [setOpen])

  const remove = useCallback(async (pid: number, cid: number): Promise<ApiError | null> => {
    stopAllIn(`${entriesKey(pid, cid)}|`)                                              // an answer being written for a conversation that is going away is stopped
    const result = await api.deleteConversation(pid, cid)
    if (!result.ok && result.error.status !== 404) return result.error                 // already gone is as good as deleted
    setEntries((previous) => {
      const { [entriesKey(pid, cid)]: _gone, ...rest } = previous
      return rest
    })
    if (activeRef.current[pid] === cid) setOpen(pid, null)
    await loadList(pid)
    return null
  }, [api, loadList, setOpen, stopAllIn])

  const forget = useCallback((pid: number) => {
    stopAllIn(`${pid}:`)
    setLists((previous) => {
      const { [pid]: _gone, ...rest } = previous
      return rest
    })
    setActive((previous) => {
      const { [pid]: _gone, ...rest } = previous
      return rest
    })
    setEntries((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) => !key.startsWith(`${pid}:`))))
  }, [stopAllIn])

  const activeId = projectId === null ? null : (active[projectId] ?? null)
  const current = projectId === null ? [] : (entries[entriesKey(projectId, activeId)] ?? [])
  return {
    conversations: projectId === null ? [] : (lists[projectId] ?? []),
    activeId,
    entries: current,
    problem,
    ask,
    retry,
    select,
    stop,
    startNew,
    remove,
    forget,
    clearProblem: () => setProblem(null),
  }
}
