export type Role = 'user' | 'assistant'

export interface Message {
  id: string
  role: Role
  content: string
  isStreaming?: boolean
}

export interface Conversation {
  id: string
  title: string
  projectName: string
}
