import { useState } from 'react'
import { Folder, FolderOpen, FolderPlus, Moon, Settings, SquarePen, Sun, Trash2, X } from 'lucide-react'
import type { Theme } from '../lib/theme'
import { ICON_BUTTON, SECTION_TITLE } from './ui'
import type { ConversationSummary, IndexStatus, Project } from '../api/types'
import { conversationLabel } from '../lib/conversation'
import { projectStatus, type Tone } from '../lib/present'

interface SidebarProps {
  collapsed: boolean
  theme: Theme
  onToggleTheme: () => void
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
  neutral: 'text-label-3',
  busy: 'text-accent',
  ok: 'text-label-3',
  warn: 'text-warn',
}

const ROW = 'group relative flex items-center rounded-control transition-colors duration-150'
const ROW_ON = 'bg-fill-strong'
const ROW_OFF = 'hover:bg-fill'
const ROW_BUTTON = 'press flex items-center rounded-control text-left transition-colors hover:bg-fill'
const CONFIRM_DELETE = 'press rounded-md px-2 py-0.5 text-xs font-medium text-danger hover:bg-danger-soft'
const CONFIRM_NO = 'press rounded-md px-2 py-0.5 text-xs text-label-2 hover:bg-fill'
const TRASH = 'press mr-1 shrink-0 rounded-md p-1 text-label-3 opacity-0 transition-opacity hover:text-danger focus:opacity-100 group-hover:opacity-100'

function Sidebar({ collapsed, theme, onToggleTheme, projects, live, selectedId, conversations, activeConversationId, canAdd, notice, onSelect, onAdd, onDelete, onSelectConversation, onNewConversation, onDeleteConversation, onDismissNotice, onOpenSettings }: SidebarProps) {
  const [confirming, setConfirming] = useState<number | null>(null)
  const [confirmingConversation, setConfirmingConversation] = useState<number | null>(null)

  return (
    <aside inert={collapsed} aria-hidden={collapsed} className={`shrink-0 overflow-hidden bg-sidebar transition-[width] duration-300 ease-[cubic-bezier(0.2,0.9,0.3,1)] motion-reduce:duration-100 ${collapsed ? 'w-0' : 'w-[256px]'}`}>
      <div className="flex h-full w-[256px] flex-col">
        <div className="drag h-[52px] shrink-0" />

        <div className="px-4 pb-2">
          <span className="block truncate text-[22px] font-semibold leading-tight tracking-[-0.02em] text-label">Clank</span>
        </div>

        <div className="px-2 pb-1">
          <button onClick={onNewConversation} disabled={selectedId === null} className={`${ROW_BUTTON} w-full gap-2.5 px-2.5 py-2 text-sm text-label disabled:opacity-40`}>
            <SquarePen className="h-4 w-4 shrink-0" strokeWidth={1.6} />
            New chat
          </button>
        </div>

        {notice && (
          <div role="alert" className="animate-rise mx-3 mb-2 flex items-start gap-1.5 rounded-control bg-danger-soft px-2.5 py-2 text-xs text-danger">
            <span className="selectable flex-1">{notice}</span>
            <button onClick={onDismissNotice} aria-label="Dismiss" className="press shrink-0 opacity-70 hover:opacity-100">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-2 pb-2">
          {selectedId !== null && (
            <div className="mt-2">
              <p className={SECTION_TITLE}>Chats</p>
              {conversations.length === 0 && <p className="px-2.5 py-1 text-xs leading-relaxed text-label-2">Your questions are saved here.</p>}
              <div className="flex flex-col gap-0.5">
                {conversations.map((conversation) => {
                  const isOpen = conversation.id === activeConversationId
                  const asking = confirmingConversation === conversation.id
                  return (
                    <div key={conversation.id} className={`${ROW} ${isOpen ? ROW_ON : ROW_OFF}`}>
                      <button onClick={() => onSelectConversation(conversation.id)} aria-current={isOpen || undefined} className="min-w-0 flex-1 truncate px-2.5 py-1.5 text-left text-sm text-label">
                        {conversationLabel(conversation)}
                      </button>
                      {asking ? (
                        <div className="flex shrink-0 items-center gap-0.5 pr-1">
                          <button onClick={() => { setConfirmingConversation(null); onDeleteConversation(conversation.id) }} className={CONFIRM_DELETE}>Delete</button>
                          <button onClick={() => setConfirmingConversation(null)} className={CONFIRM_NO}>No</button>
                        </div>
                      ) : (
                        <button onClick={() => setConfirmingConversation(conversation.id)} aria-label={`Delete conversation ${conversationLabel(conversation)}`} className={TRASH}>
                          <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div className="mt-4">
            <p className={SECTION_TITLE}>Projects</p>
            <div className="flex flex-col gap-0.5">
              {projects.map((project) => {
                const isActive = project.id === selectedId
                const status = projectStatus(project, live[project.id])
                const asking = confirming === project.id
                const Icon = isActive ? FolderOpen : Folder
                return (
                  <div key={project.id} className={`${ROW} ${isActive ? ROW_ON : ROW_OFF}`}>
                    <button onClick={() => onSelect(project.id)} aria-current={isActive || undefined} className="flex min-w-0 flex-1 items-center gap-2.5 px-2.5 py-1.5 text-left">
                      <Icon className="h-4 w-4 shrink-0 text-label-2" strokeWidth={1.6} />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="truncate text-sm text-label">{project.name}</span>
                        <span className={`truncate text-xs ${TONE[status.tone]}`}>{status.label}</span>
                      </span>
                    </button>
                    {asking ? (
                      <div className="flex shrink-0 items-center gap-0.5 pr-1">
                        <button onClick={() => { setConfirming(null); onDelete(project.id) }} className={CONFIRM_DELETE}>Delete</button>
                        <button onClick={() => setConfirming(null)} className={CONFIRM_NO}>No</button>
                      </div>
                    ) : (
                      <button onClick={() => setConfirming(project.id)} aria-label={`Delete ${project.name}`} className={TRASH}>
                        <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                      </button>
                    )}
                  </div>
                )
              })}
              {projects.length === 0 && <p className="px-2.5 py-1 text-xs leading-relaxed text-label-2">No projects yet. Add a folder to start asking questions about its code.</p>}
              <button onClick={onAdd} disabled={!canAdd} className={`${ROW_BUTTON} gap-2.5 px-2.5 py-1.5 text-sm text-label-2 hover:text-label disabled:opacity-40`}>
                <FolderPlus className="h-4 w-4 shrink-0" strokeWidth={1.6} />
                Add project
              </button>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1 p-2">
          <button onClick={onOpenSettings} className={`${ROW_BUTTON} min-w-0 flex-1 gap-2.5 px-2.5 py-2 text-sm text-label-2 hover:text-label`}>
            <Settings className="h-4 w-4 shrink-0" strokeWidth={1.6} />
            Answer model
          </button>
          <button onClick={onToggleTheme} aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'} title={theme === 'dark' ? 'Light mode' : 'Dark mode'} className={`${ICON_BUTTON} !p-2`}>
            {theme === 'dark' ? <Sun className="h-4 w-4" strokeWidth={1.6} /> : <Moon className="h-4 w-4" strokeWidth={1.6} />}
          </button>
        </div>
      </div>
    </aside>
  )
}

export default Sidebar
