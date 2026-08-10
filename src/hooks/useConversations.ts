import { useCallback, useEffect, useRef, useState } from 'react'
import type { Conversation, Message } from '../types'
import { assistantReplyPool, initialConversations, initialMessages } from '../data/mockConversations'

let idCounter = 0
function nextId(prefix: string) {
  idCounter += 1
  return `${prefix}-${Date.now()}-${idCounter}`
}

const STREAM_CHARS_PER_TICK = 2
const STREAM_TICK_MS = 20

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>(initialConversations)
  const [messagesByConversation, setMessagesByConversation] = useState<Record<string, Message[]>>(initialMessages)
  const [activeId, setActiveId] = useState<string>(initialConversations[0]?.id ?? '')
  const [streamingIds, setStreamingIds] = useState<Set<string>>(new Set())
  const timersRef = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  useEffect(() => {
    const timers = timersRef.current
    return () => {
      Object.values(timers).forEach(clearInterval)
    }
  }, [])

  const appendMessage = useCallback((conversationId: string, message: Message) => {
    setMessagesByConversation((prev) => ({
      ...prev,
      [conversationId]: [...(prev[conversationId] ?? []), message],
    }))
  }, [])

  const addConversation = useCallback(() => {
    const id = nextId('c')
    const conversation: Conversation = {
      id,
      title: 'New conversation',
      projectName: 'clank',
    }
    setConversations((prev) => [conversation, ...prev])
    setMessagesByConversation((prev) => ({ ...prev, [id]: [] }))
    setActiveId(id)
  }, [])

  const startAssistantReply = useCallback((conversationId: string) => {
    const fullText = assistantReplyPool[Math.floor(Math.random() * assistantReplyPool.length)]
    const replyId = nextId('m')

    appendMessage(conversationId, { id: replyId, role: 'assistant', content: '', isStreaming: true })
    setStreamingIds((prev) => new Set(prev).add(conversationId))

    let revealed = 0
    const timer = setInterval(() => {
      revealed = Math.min(fullText.length, revealed + STREAM_CHARS_PER_TICK)
      const done = revealed >= fullText.length

      setMessagesByConversation((prev) => {
        const list = prev[conversationId] ?? []
        return {
          ...prev,
          [conversationId]: list.map((m) =>
            m.id === replyId ? { ...m, content: fullText.slice(0, revealed), isStreaming: !done } : m,
          ),
        }
      })

      if (done) {
        clearInterval(timer)
        delete timersRef.current[conversationId]
        setStreamingIds((prev) => {
          const next = new Set(prev)
          next.delete(conversationId)
          return next
        })
      }
    }, STREAM_TICK_MS)

    timersRef.current[conversationId] = timer
  }, [appendMessage])

  const sendMessage = useCallback(
    (conversationId: string, content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      appendMessage(conversationId, { id: nextId('m'), role: 'user', content: trimmed })
      startAssistantReply(conversationId)
    },
    [appendMessage, startAssistantReply],
  )

  return {
    conversations,
    messagesByConversation,
    activeId,
    setActiveId,
    addConversation,
    sendMessage,
    streamingIds,
  }
}
