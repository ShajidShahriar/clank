import { useCallback, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar'
import ChatPane from './components/ChatPane'
import SettingsDialog from './components/SettingsDialog'
import SourceViewer from './components/SourceViewer'
import type { Source } from './api/types'
import { createApi } from './api/client'
import { getBackendControl, getBridge } from './bridge'
import { useAllowRemote } from './hooks/useAllowRemote'
import { useChats } from './hooks/useChats'
import { useBackendStatus } from './hooks/useBackendStatus'
import { useProjects } from './hooks/useProjects'
import { useSetup } from './hooks/useSetup'
import type { ErrorAction } from './lib/present'
import type { Entry } from './types'

function App() {
  const api = useMemo(() => createApi(getBridge()), [])
  const backend = useBackendStatus()
  const ready = backend.state === 'ready'
  const [allowRemote, setAllowRemote] = useAllowRemote()
  const projects = useProjects(api, ready)
  const setup = useSetup(api, ready)
  const [notice, setNotice] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [viewing, setViewing] = useState<{ projectId: number, source: Source } | null>(null)

  const selected = projects.projects.find((p) => p.id === projects.selectedId)
  const live = selected ? projects.live[selected.id] : undefined
  const indexing = selected ? ['running', 'cancelling'].includes(live?.state ?? selected.index_state) : false
  const chats = useChats(api, ready, selected?.id ?? null)
  const entries = selected ? chats.entries : []

  const restartBackend = useCallback(() => { void getBackendControl()?.restart() }, [])

  const addProject = useCallback(async () => {
    const error = await projects.addProject()
    setNotice(error ? error.message : null)
  }, [projects])

  const deleteProject = useCallback(async (id: number) => {
    const error = await projects.removeProject(id)
    if (error) setNotice(error.message)
    else chats.forget(id)
  }, [projects, chats])

  const deleteConversation = useCallback(async (conversationId: number) => {
    if (!selected) return
    const error = await chats.remove(selected.id, conversationId)
    if (error) setNotice(error.message)
  }, [selected, chats])

  const onAction = useCallback((action: ErrorAction, entry: Entry) => {
    if (!selected) return
    if (action === 'allow_remote') {
      setAllowRemote(true)                                      // the person chose to allow it: remembered, and the same question is asked again
      void chats.retry(selected.id, entry, true)
    } else if (action === 'retry' || action === 'wait') {
      void chats.retry(selected.id, entry, allowRemote)
    } else if (action === 'index') {
      void projects.startIndex(selected.id)
    } else if (action === 'restart_backend') {
      restartBackend()
    } else if (action === 'refresh') {
      void projects.refresh()
    } else if (action === 'settings') {
      setSettingsOpen(true)
    }
  }, [selected, chats, allowRemote, projects, setAllowRemote, restartBackend])

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar
        projects={projects.projects}
        live={projects.live}
        selectedId={projects.selectedId}
        conversations={chats.conversations}
        activeConversationId={chats.activeId}
        canAdd={ready}
        notice={notice ?? (projects.problem && ready ? projects.problem.message : null) ?? (chats.problem && ready ? chats.problem.message : null)}
        onSelect={projects.setSelectedId}
        onAdd={addProject}
        onDelete={deleteProject}
        onSelectConversation={(id) => selected && chats.select(selected.id, id)}
        onNewConversation={() => selected && chats.startNew(selected.id)}
        onDeleteConversation={deleteConversation}
        onDismissNotice={() => { setNotice(null); chats.clearProblem() }}
        onOpenSettings={() => setSettingsOpen(true)}
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
        onAsk={(question) => selected && void chats.ask(selected.id, question, allowRemote)}
        onAction={onAction}
        onStop={() => selected && chats.stop(selected.id)}
        onOpenSource={(source) => selected && setViewing({ projectId: selected.id, source })}
        onRestartBackend={restartBackend}
        setup={{ view: setup.view, starting: setup.starting, actionError: setup.actionError, onPull: () => void setup.startPull(), onCheck: setup.checkAgain }}
      />
      {viewing && viewing.projectId === selected?.id && <SourceViewer api={api} projectId={viewing.projectId} source={viewing.source} onClose={() => setViewing(null)} />}
      {settingsOpen && <SettingsDialog api={api} onClose={() => setSettingsOpen(false)} onSaved={() => {}} />}
    </div>
  )
}

export default App
