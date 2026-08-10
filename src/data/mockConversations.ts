import type { Conversation, Message } from '../types'

export const initialConversations: Conversation[] = [
  { id: 'c1', title: 'Fix flaky login test', projectName: 'clank' },
  { id: 'c2', title: 'Add dark mode toggle', projectName: 'clank' },
  { id: 'c3', title: 'Refactor auth middleware', projectName: 'api-server' },
  { id: 'c4', title: 'Investigate memory leak', projectName: 'worker' },
]

export const initialMessages: Record<string, Message[]> = {
  c1: [
    { id: 'm1', role: 'user', content: 'The login test keeps failing intermittently in CI, can you take a look?' },
    { id: 'm2', role: 'assistant', content: "Sure, I'll dig into it. Can you point me to the test file, or should I search for it?" },
    { id: 'm3', role: 'user', content: "It's in src/tests/auth/login.test.ts" },
    {
      id: 'm4',
      role: 'assistant',
      content:
        "Found it. Looks like the test is racing against an async redirect — it's not awaiting the navigation promise before asserting on the URL. I'll add the missing await and a small timeout guard.",
    },
  ],
  c2: [],
  c3: [],
  c4: [],
}

export const assistantReplyPool = [
  'Got it — let me take a look and get back to you.',
  "I found the issue. It looks like state isn't resetting between renders. I can patch that for you.",
  "Here's what I'd suggest: pull that logic into a small hook so it's easier to test in isolation.",
  'Let me pull up the file and see what’s going on before I make any changes.',
  'That should be a quick fix. Give me a moment to apply it and run the tests.',
]
