import { Bot } from 'lucide-react'
import type { Message } from '../types'

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex items-start gap-2 ${isUser ? 'justify-end' : 'justify-start'}`}>
      {!isUser && (
        <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800">
          <Bot className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
        </div>
      )}
      <div
        className={`max-w-[75%] whitespace-pre-wrap break-words rounded-lg px-3.5 py-2 text-sm leading-relaxed ${
          isUser
            ? 'bg-gray-900 text-white dark:bg-white dark:text-black'
            : 'border border-gray-200 bg-white text-gray-900 dark:border-white/10 dark:bg-[#0a0a0a] dark:text-gray-100'
        }`}
      >
        {message.content}
        {message.isStreaming && (
          <span className="-mb-0.5 ml-0.5 inline-block h-3.5 w-1.5 animate-pulse align-middle bg-gray-400 dark:bg-gray-500" />
        )}
      </div>
    </div>
  )
}

export default MessageBubble
