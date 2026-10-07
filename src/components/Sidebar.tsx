import { useState } from 'react'
import { FolderPlus, MessageSquarePlus, Settings, Trash2, X } from 'lucide-react'
import logoDark from '../assets/logo_dark.png'
import logoLight from '../assets/logo_light.png'
import type { ConversationSummary, IndexStatus, Project } from '../api/types'
import { conversationLabel } from '../lib/conversation'
import { projectStatus, type Tone } from '../lib/present'

interface SidebarProps {
  projects: Project[]
  live: Record<number, IndexStatus>
  selectedId: number | null
  conversations: ConversationSummary[]
  activeConversationId: number | null
  canAdd: boolean
  notice: string | null
  onSelect: (id: number) => void
  onAdd: () => void
  onDelete: (id: number) => void
  onSelectConversation: (id: number) => void
  onNewConversation: () => void
  onDeleteConversation: (id: number) => void
  onDismissNotice: () => void
  onOpenSettings: () => void
}

const TONE: Record<Tone, string> = {
  neutral: 'text-gray-500 dark:text-gray-500',
  busy: 'text-blue-600 dark:text-blue-400',
  ok: 'text-gray-500 dark:text-gray-500',
  warn: 'text-amber-600 dark:text-amber-400',
}

function Sidebar({ projects, live, selectedId, conversations, activeConversationId, canAdd, notice, onSelect, onAdd, onDelete, onSelectConversation, onNewConversation, onDeleteConversation, onDismissNotice, onOpenSettings }: SidebarProps) {
  const [confirming, setConfirming] = useState<number | null>(null)
  const [confirmingConversation, setConfirmingConversation] = useState<number | null>(null)

  return (
    <aside className="flex w-[240px] shrink-0 flex-col border-r border-gray-200 bg-gray-50 dark:border-white/10 dark:bg-[#0a0a0a]">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-gray-200 px-3 dark:border-white/10">
        <img src={logoLight} alt="Clank" className="h-5 w-5 shrink-0 dark:hidden" />
        <img src={logoDark} alt="Clank" className="hidden h-5 w-5 shrink-0 dark:block" />
        <span className="truncate text-sm font-semibold text-gray-900 dark:text-white">Clank</span>
      </div>

      <div className="p-2">
        <button
          onClick={onAdd}
          disabled={!canAdd}
          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:text-gray-300 dark:hover:bg-gray-800"
        >
          <FolderPlus className="h-3.5 w-3.5 shrink-0" />
          Add project
        </button>
      </div>

      {notice && (
        <div className="mx-2 mb-2 flex items-start gap-1.5 rounded-md border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
          <span className="flex-1">{notice}</span>
          <button onClick={onDismissNotice} aria-label="Dismiss" className="shrink-0 opacity-70 hover:opacity-100">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        <p className="px-1.5 py-1 text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-500">Projects</p>
        {projects.length === 0 && (
          <p className="px-1.5 py-2 text-xs leading-relaxed text-gray-500 dark:text-gray-400">No projects yet. Add a folder to start asking questions about its code.</p>
        )}
        <div className="mt-1 flex flex-col gap-0.5">
          {projects.map((project) => {
            const isActive = project.id === selectedId
            const status = projectStatus(project, live[project.id])
            const asking = confirming === project.id
            return (
              <div
                key={project.id}
                className={`group relative flex items-center rounded-md transition-all duration-150 ${
                  isActive ? 'bg-gray-100 dark:bg-gray-900' : 'hover:bg-gray-100/60 dark:hover:bg-gray-900/60'
                }`}
              >
                <button onClick={() => onSelect(project.id)} className="flex min-w-0 flex-1 flex-col items-start gap-0.5 px-2.5 py-1.5 text-left">
                  <span className={`w-full truncate text-sm ${isActive ? 'font-medium text-gray-900 dark:text-white' : 'text-gray-700 dark:text-gray-300'}`}>
                    {project.name}
                  </span>
                  <span className={`w-full truncate text-xs ${TONE[status.tone]}`}>{status.label}</span>
                </button>
                {asking ? (
                  <div className="flex shrink-0 items-center gap-1 pr-1.5 text-xs">
                    <button onClick={() => { setConfirming(null); onDelete(project.id) }} className="rounded px-1.5 py-0.5 font-medium text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10">
                      Delete
                    </button>
                    <button onClick={() => setConfirming(null)} className="rounded px-1.5 py-0.5 text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-800">
                      No
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setConfirming(project.id)}
                    aria-label={`Delete ${project.name}`}
                    className="mr-1.5 shrink-0 rounded p-1 text-gray-400 opacity-0 transition-opacity hover:text-red-600 focus:opacity-100 group-hover:opacity-100 dark:hover:text-red-400"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            )
          })}
        </div>

        {selectedId !== null && (
          <div className="mt-3">
            <div className="flex items-center justify-between px-1.5 py-1">
              <p className="text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-500">Conversations</p>
              <button onClick={onNewConversation} aria-label="New conversation" title="New conversation" className="rounded p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-900 dark:hover:text-white">
                <MessageSquarePlus className="h-3.5 w-3.5" />
              </button>
            </div>
            {conversations.length === 0 && <p className="px-1.5 py-1 text-xs leading-relaxed text-gray-500 dark:text-gray-400">Your questions are saved here.</p>}
            <div className="flex flex-col gap-0.5">
              {conversations.map((conversation) => {
                const isOpen = conversation.id === activeConversationId
                const asking = confirmingConversation === conversation.id
                return (
                  <div key={conversation.id} className={`group flex items-center rounded-md ${isOpen ? 'bg-gray-100 dark:bg-gray-900' : 'hover:bg-gray-100/60 dark:hover:bg-gray-900/60'}`}>
                    <button onClick={() => onSelectConversation(conversation.id)} className={`min-w-0 flex-1 truncate px-2.5 py-1.5 text-left text-xs ${isOpen ? 'font-medium text-gray-900 dark:text-white' : 'text-gray-700 dark:text-gray-300'}`}>
                      {conversationLabel(conversation)}
                    </button>
                    {asking ? (
                      <div className="flex shrink-0 items-center gap-1 pr-1.5 text-xs">
                        <button onClick={() => { setConfirmingConversation(null); onDeleteConversation(conversation.id) }} className="rounded px-1.5 py-0.5 font-medium text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-500/10">Delete</button>
                        <button onClick={() => setConfirmingConversation(null)} className="rounded px-1.5 py-0.5 text-gray-500 hover:bg-gray-200 dark:hover:bg-gray-800">No</button>
                      </div>
                    ) : (
                      <button onClick={() => setConfirmingConversation(conversation.id)} aria-label={`Delete conversation ${conversationLabel(conversation)}`} className="mr-1.5 shrink-0 rounded p-1 text-gray-400 opacity-0 transition-opacity hover:text-red-600 focus:opacity-100 group-hover:opacity-100 dark:hover:text-red-400">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-gray-200 p-2 dark:border-white/10">
        <button
          onClick={onOpenSettings}
          className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-xs text-gray-600 transition-colors hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-900"
        >
          <Settings className="h-3.5 w-3.5 shrink-0" />
          Answer model
        </button>
      </div>
    </aside>
  )
}

export default Sidebar
