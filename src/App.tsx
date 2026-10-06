import { useCallback, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar'
import ChatPane from './components/ChatPane'
import { createApi } from './api/client'
import { getBackendControl, getBridge } from './bridge'
import { useAllowRemote } from './hooks/useAllowRemote'
import { useAnswers } from './hooks/useAnswers'
import { useBackendStatus } from './hooks/useBackendStatus'
import { useProjects } from './hooks/useProjects'
import type { ErrorAction } from './lib/present'
import type { Entry } from './types'

function App() {
  const api = useMemo(() => createApi(getBridge()), [])
  const backend = useBackendStatus()
  const ready = backend.state === 'ready'
  const [allowRemote, setAllowRemote] = useAllowRemote()
  const projects = useProjects(api, ready)
  const answers = useAnswers(api)
  const [notice, setNotice] = useState<string | null>(null)

  const selected = projects.projects.find((p) => p.id === projects.selectedId)
  const live = selected ? projects.live[selected.id] : undefined
  const indexing = selected ? ['running', 'cancelling'].includes(live?.state ?? selected.index_state) : false
  const entries = selected ? (answers.byProject[selected.id] ?? []) : []

  const restartBackend = useCallback(() => { void getBackendControl()?.restart() }, [])

  const addProject = useCallback(async () => {
    const error = await projects.addProject()
    setNotice(error ? error.message : null)
  }, [projects])

  const deleteProject = useCallback(async (id: number) => {
    const error = await projects.removeProject(id)
    if (error) setNotice(error.message)
    else answers.forget(id)
  }, [projects, answers])

  const onAction = useCallback((action: ErrorAction, entry: Entry) => {
    if (!selected) return
    if (action === 'allow_remote') {
      setAllowRemote(true)                                      // the person chose to allow it: remembered, and the same question is asked again
      answers.retry(selected.id, entry, true)
    } else if (action === 'retry' || action === 'wait') {
      answers.retry(selected.id, entry, allowRemote)
    } else if (action === 'index') {
      void projects.startIndex(selected.id)
    } else if (action === 'restart_backend') {
      restartBackend()
    } else if (action === 'refresh') {
      void projects.refresh()
    }
  }, [selected, answers, allowRemote, projects, setAllowRemote, restartBackend])

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar
        projects={projects.projects}
        live={projects.live}
        selectedId={projects.selectedId}
        canAdd={ready}
        notice={notice ?? (projects.problem && ready ? projects.problem.message : null)}
        onSelect={projects.setSelectedId}
        onAdd={addProject}
        onDelete={deleteProject}
        onDismissNotice={() => setNotice(null)}
      />
      <ChatPane
        backend={backend}
        project={selected}
        index={live}
        entries={entries}
        allowRemote={allowRemote}
        indexing={indexing}
        onToggleRemote={setAllowRemote}
        onIndex={() => selected && void projects.startIndex(selected.id)}
        onCancel={() => selected && void projects.cancelIndex(selected.id)}
        onAsk={(question) => selected && answers.ask(selected.id, question, allowRemote)}
        onAction={onAction}
        onRestartBackend={restartBackend}
      />
    </div>
  )
}

export default App
