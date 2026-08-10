import { Plus } from 'lucide-react'
import logoDark from '../assets/logo_dark.png'
import logoLight from '../assets/logo_light.png'
import type { Conversation } from '../types'

interface SidebarProps {
  conversations: Conversation[]
  activeId: string
  onSelect: (id: string) => void
  onNew: () => void
}

function Sidebar({ conversations, activeId, onSelect, onNew }: SidebarProps) {
  return (
    <aside className="flex w-[220px] shrink-0 flex-col border-r border-gray-200 bg-gray-50 dark:border-white/10 dark:bg-[#0a0a0a]">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-gray-200 px-3 dark:border-white/10">
        <img src={logoLight} alt="Clank" className="h-5 w-5 shrink-0 dark:hidden" />
        <img src={logoDark} alt="Clank" className="hidden h-5 w-5 shrink-0 dark:block" />
        <span className="truncate text-sm font-semibold text-gray-900 dark:text-white">Clank</span>
      </div>

      <div className="p-2">
        <button
          onClick={onNew}
          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 dark:border-white/10 dark:text-gray-300 dark:hover:bg-gray-800"
        >
          <Plus className="h-3.5 w-3.5 shrink-0" />
          New conversation
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        <p className="px-1.5 py-1 text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-500">
          Conversations
        </p>
        <div className="mt-1 flex flex-col gap-0.5">
          {conversations.map((conversation) => {
            const isActive = conversation.id === activeId
            return (
              <button
                key={conversation.id}
                onClick={() => onSelect(conversation.id)}
                className={`flex w-full flex-col items-start gap-0.5 rounded-md px-2.5 py-1.5 text-left transition-all duration-150 ${
                  isActive
                    ? 'bg-gray-100 dark:bg-gray-900'
                    : 'hover:bg-gray-100/60 dark:hover:bg-gray-900/60'
                }`}
              >
                <span
                  className={`w-full truncate text-sm ${
                    isActive
                      ? 'font-medium text-gray-900 dark:text-white'
                      : 'text-gray-700 dark:text-gray-300'
                  }`}
                >
                  {conversation.title}
                </span>
                <span className="w-full truncate text-xs text-gray-500 dark:text-gray-500">
                  {conversation.projectName}
                </span>
              </button>
            )
          })}
        </div>
      </div>
    </aside>
  )
}

export default Sidebar
