import type { Conversation, Message } from '../types'
import MessageList from './MessageList'
import MessageInput from './MessageInput'

interface ChatPaneProps {
  conversation: Conversation | undefined
  messages: Message[]
  onSend: (text: string) => void
  isStreaming: boolean
}

function ChatPane({ conversation, messages, onSend, isStreaming }: ChatPaneProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col bg-white dark:bg-black">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-gray-200 px-4 dark:border-white/10">
        <p className="truncate text-sm font-semibold text-gray-900 dark:text-white">
          {conversation?.title ?? 'Select a conversation'}
        </p>
        {conversation && (
          <span className="shrink-0 text-xs text-gray-500 dark:text-gray-500">· {conversation.projectName}</span>
        )}
      </header>

      <MessageList messages={messages} />
      <MessageInput onSend={onSend} disabled={isStreaming} />
    </div>
  )
}

export default ChatPane
