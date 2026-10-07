// How saved messages become the entries the window draws. Pure (no React, no browser), tested in conversation.test.ts. Erasable TypeScript only.
import { isAnswer } from '../api/client.ts'
import type { SavedMessage } from '../api/types.ts'
import type { Entry } from '../types.ts'

/** A saved question and its answer, as the entry a live answer would have made. The answer's text is the message; its extras are the message's meta. */
export function entriesFromMessages(messages: SavedMessage[]): Entry[] {
  const entries: Entry[] = []
  let question: SavedMessage | null = null
  const missing = (asked: SavedMessage): Entry => ({
    id: `m${asked.id}`, question: asked.content, state: 'error',
    error: { code: 'saved_answer_missing', message: 'This question has no saved answer.', status: 0 },
  })
  for (const message of messages) {
    if (message.role === 'user') {
      if (question) entries.push(missing(question))
      question = message
    } else if (question) {
      const answer = { ...(message.meta ?? {}), answer: message.content }
      entries.push(isAnswer(answer)
        ? { id: `m${question.id}`, question: question.content, state: 'done', answer }
        : { id: `m${question.id}`, question: question.content, state: 'error', error: { code: 'saved_answer_unreadable', message: 'This saved answer could not be read. It may have been saved by a different version of Clank.', status: 0 } })
      question = null
    }
  }
  if (question) entries.push(missing(question))
  return entries
}

export function conversationLabel(conversation: { title: string | null }): string {
  return conversation.title !== null && conversation.title.trim() !== '' ? conversation.title : 'New conversation'
}

/** Where the entries of one conversation (or of the not yet created one of a project) are kept. */
export function entriesKey(projectId: number, conversationId: number | null): string {
  return conversationId === null ? `${projectId}:draft` : `${projectId}:${conversationId}`
}
