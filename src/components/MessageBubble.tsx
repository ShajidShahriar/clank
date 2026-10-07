import { memo } from 'react'

/** A question the person asked. */
function MessageBubble({ text }: { text: string }) {
  return (
    <div className="animate-rise flex justify-end">
      <div className="selectable max-w-[78%] whitespace-pre-wrap break-words rounded-bubble rounded-br-md bg-accent px-3.5 py-2 text-sm leading-[1.4] text-white">
        {text}
      </div>
    </div>
  )
}

export default memo(MessageBubble)
