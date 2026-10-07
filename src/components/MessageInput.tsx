import { useState, type FormEvent } from 'react'
import { ArrowUp, Square } from 'lucide-react'

interface MessageInputProps {
  onSend: (text: string) => void
  disabled?: boolean
  placeholder: string
  /** An answer is being written: the send button becomes a Stop button. */
  streaming?: boolean
  onStop?: () => void
}

function MessageInput({ onSend, disabled, placeholder, streaming, onStop }: MessageInputProps) {
  const [value, setValue] = useState('')

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!value.trim() || disabled) return
    onSend(value.trim())
    setValue('')
  }

  return (
    <form onSubmit={handleSubmit} className="shrink-0 border-t border-gray-200 p-3 dark:border-white/10">
      <div className="flex items-end gap-2">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          maxLength={2000}
          className="flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 placeholder-gray-400 transition-all focus:border-transparent focus:outline-none focus:ring-2 focus:ring-gray-300 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-[#0a0a0a] dark:text-white dark:placeholder-gray-500 dark:focus:ring-gray-700"
        />
        {streaming ? (
          <button
            type="button"
            onClick={onStop}
            aria-label="Stop"
            title="Stop the answer"
            className="shrink-0 rounded-lg bg-gray-900 p-2 text-white transition-colors hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-gray-100"
          >
            <Square className="h-4 w-4 fill-current" />
          </button>
        ) : (
          <button
            type="submit"
            disabled={disabled || !value.trim()}
            aria-label="Ask"
            className="shrink-0 rounded-lg bg-gray-900 p-2 text-white transition-colors hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-gray-100"
          >
            <ArrowUp className="h-4 w-4" />
          </button>
        )}
      </div>
    </form>
  )
}

export default MessageInput
