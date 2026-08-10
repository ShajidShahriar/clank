import { useEffect, useRef } from 'react'
import { MessageCircle } from 'lucide-react'
import type { Message } from '../types'
import MessageBubble from './MessageBubble'

function MessageList({ messages }: { messages: Message[] }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages])

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
        <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 dark:border-white/20">
          <MessageCircle className="h-5 w-5 text-gray-400" />
        </div>
        <p className="text-xs leading-relaxed text-gray-500 dark:text-gray-400">
          No messages yet.
          <br />
          Say hello to start the conversation.
        </p>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
    </div>
  )
}

export default MessageList
