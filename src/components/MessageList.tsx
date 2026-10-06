import { useEffect, useRef } from 'react'
import { Bot, Loader2, MessageCircle } from 'lucide-react'
import type { Entry } from '../types'
import type { ErrorAction } from '../lib/present'
import MessageBubble from './MessageBubble'
import AnswerCard from './AnswerCard'
import ErrorCard from './ErrorCard'

interface MessageListProps {
  entries: Entry[]
  hint: string
  onAction: (action: ErrorAction, entry: Entry) => void
}

function MessageList({ entries, hint, onAction }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [entries])

  if (entries.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center px-6 text-center">
        <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 dark:border-white/20">
          <MessageCircle className="h-5 w-5 text-gray-400" />
        </div>
        <p className="max-w-sm text-xs leading-relaxed text-gray-500 dark:text-gray-400">{hint}</p>
      </div>
    )
  }

  return (
    <div ref={containerRef} className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
      {entries.map((entry) => (
        <div key={entry.id} className="flex flex-col gap-3">
          <MessageBubble text={entry.question} />
          {entry.state === 'pending' && (
            <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
              <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800">
                <Bot className="h-3.5 w-3.5" />
              </div>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Searching the code and asking the model…
            </div>
          )}
          {entry.state === 'done' && entry.answer && <AnswerCard answer={entry.answer} />}
          {entry.state === 'error' && <ErrorCard entry={entry} onAction={onAction} />}
        </div>
      ))}
    </div>
  )
}

export default MessageList
