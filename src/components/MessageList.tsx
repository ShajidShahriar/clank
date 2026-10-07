import { useEffect, useRef } from 'react'
import type { Source } from '../api/types'
import type { Entry } from '../types'
import { isNearBottom } from '../lib/scroll'
import type { ErrorAction } from '../lib/present'
import MessageBubble from './MessageBubble'
import AnswerCard from './AnswerCard'
import ErrorCard from './ErrorCard'
import LiveAnswer from './LiveAnswer'
import PartialAnswer from './PartialAnswer'

interface MessageListProps {
  entries: Entry[]
  onAction: (action: ErrorAction, entry: Entry) => void
  onOpenSource: (source: Source) => void
}

function MessageList({ entries, onAction, onOpenSource }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const following = useRef(true)                                   // is the person at the bottom? Then new text is followed; if they scrolled up to re-read, it is not
  const count = useRef(entries.length)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    if (entries.length > count.current) following.current = true       // a new question is the person's own act: follow it
    count.current = entries.length
    if (following.current) el.scrollTop = el.scrollHeight
  }, [entries])

  return (
    <div ref={containerRef} onScroll={() => { if (containerRef.current) following.current = isNearBottom(containerRef.current) }} className="flex flex-1 flex-col overflow-y-auto px-5 py-6">
      <div className="mx-auto flex w-full max-w-[720px] flex-col gap-6">
      {entries.map((entry) => (
        <div key={entry.id} className="flex flex-col gap-4">
          <MessageBubble text={entry.question} />
          {(entry.state === 'pending' || entry.state === 'streaming') && <LiveAnswer entry={entry} onOpenSource={onOpenSource} />}
          {entry.state === 'done' && entry.answer && <AnswerCard answer={entry.answer} onOpenSource={onOpenSource} />}
          {(entry.state === 'stopped' || (entry.state === 'error' && entry.partial)) && <PartialAnswer entry={entry} onAction={onAction} />}
          {entry.state === 'error' && <ErrorCard entry={entry} onAction={onAction} />}
        </div>
      ))}
      </div>
    </div>
  )
}

export default MessageList
