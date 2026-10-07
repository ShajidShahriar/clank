import { memo } from 'react'

/** A question the person asked. */
function MessageBubble({ text }: { text: string }) {
  return (
    <div className="flex items-start justify-end gap-2">
      <div className="max-w-[75%] whitespace-pre-wrap break-words rounded-lg bg-gray-900 px-3.5 py-2 text-sm leading-relaxed text-white dark:bg-white dark:text-black">
        {text}
      </div>
    </div>
  )
}

export default memo(MessageBubble)
