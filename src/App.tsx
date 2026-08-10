import { useEffect } from 'react'
import Sidebar from './components/Sidebar'
import ChatPane from './components/ChatPane'
import { useConversations } from './hooks/useConversations'

function App() {
  useEffect(() => {
    fetch("http://127.0.0.1:8000/health")
      .then(res => res.json())
      .then(data => console.log("Backend says:", data))
      .catch(err => console.error("Backend not reachable:", err));
  }, []);

  const {
    conversations,
    messagesByConversation,
    activeId,
    setActiveId,
    addConversation,
    sendMessage,
    streamingIds,
  } = useConversations()

  const activeConversation = conversations.find((c) => c.id === activeId)
  const messages = messagesByConversation[activeId] ?? []

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={setActiveId}
        onNew={addConversation}
      />
      <ChatPane
        conversation={activeConversation}
        messages={messages}
        onSend={(text) => sendMessage(activeId, text)}
        isStreaming={streamingIds.has(activeId)}
      />
    </div>
  )
}

export default App
