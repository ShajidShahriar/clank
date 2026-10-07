import type { IndexStatus, Project, Source } from '../api/types'
import type { BackendView, Entry } from '../types'
import type { ErrorAction } from '../lib/present'
import BackendBanner from './BackendBanner'
import SetupCard from './SetupCard'
import type { SetupView } from '../lib/setup'
import MessageInput from './MessageInput'
import MessageList from './MessageList'
import ProjectHeader from './ProjectHeader'

interface ChatPaneProps {
  backend: BackendView
  project: Project | undefined
  index: IndexStatus | undefined
  entries: Entry[]
  allowRemote: boolean
  indexing: boolean
  onToggleRemote: (value: boolean) => void
  onIndex: () => void
  onCancel: () => void
  onAsk: (question: string) => void
  onAction: (action: ErrorAction, entry: Entry) => void
  onOpenSource: (source: Source) => void
  onStop: () => void
  onRestartBackend: () => void
  setup: { view: SetupView, starting: boolean, actionError: string | null, onPull: () => void, onCheck: () => void }
}

function ChatPane({ backend, project, index, entries, allowRemote, indexing, onToggleRemote, onIndex, onCancel, onAsk, onAction, onOpenSource, onStop, onRestartBackend, setup }: ChatPaneProps) {
  const ready = backend.state === 'ready'
  const streaming = entries.some((entry) => entry.state === 'pending' || entry.state === 'streaming')       // an answer is being written: the person can stop it

  let placeholder = 'Ask about this code…'
  let hint = 'Ask a question about this project. Clank finds the relevant code and asks the model to answer from it.'
  let locked = streaming
  if (!ready) {
    placeholder = 'The backend is not running'
    locked = true
  } else if (!project) {
    placeholder = 'Add a project first'
    hint = 'Add a project folder with the button on the left, then ask questions about its code.'
    locked = true
  } else if (indexing) {
    placeholder = 'Indexing…'
    hint = 'Clank is reading this project. You can ask as soon as it has finished.'
    locked = true
  } else if (!project.indexed) {
    placeholder = 'Index this project to ask questions'
    hint = 'This project has not been indexed yet. Press Index so Clank can search its code.'
    locked = true
  } else if (streaming) {
    placeholder = 'Writing the answer…'
  }

  return (
    <div className="flex min-w-0 flex-1 flex-col bg-white dark:bg-black">
      <BackendBanner backend={backend} onRestart={onRestartBackend} />
      <SetupCard view={setup.view} busy={setup.starting} actionError={setup.actionError} onPull={setup.onPull} onCheck={setup.onCheck} />
      <ProjectHeader project={project} index={index} allowRemote={allowRemote} disabled={!ready} onIndex={onIndex} onCancel={onCancel} onToggleRemote={onToggleRemote} />
      <MessageList entries={entries} hint={hint} onAction={onAction} onOpenSource={onOpenSource} />
      <MessageInput onSend={onAsk} disabled={locked} placeholder={placeholder} streaming={streaming && ready} onStop={onStop} />
    </div>
  )
}

export default ChatPane
