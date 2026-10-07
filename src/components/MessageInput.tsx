import { useState, type FormEvent } from 'react'
import { ArrowUp, Square } from 'lucide-react'

interface MessageInputProps {
  onSend: (text: string) => void
  disabled?: boolean
  placeholder: string
  /** An answer is being written: the send button becomes a Stop button. */
  streaming?: boolean
  onStop?: () => void
  /** The first view of a conversation: the bar sits in the middle, under the heading, not at the bottom. */
  centered?: boolean
}

const ROUND = 'press flex h-8 w-8 shrink-0 items-center justify-center rounded-full'

function MessageInput({ onSend, disabled, placeholder, streaming, onStop, centered }: MessageInputProps) {
  const [value, setValue] = useState('')

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!value.trim() || disabled) return
    onSend(value.trim())
    setValue('')
  }

  return (
    <form onSubmit={handleSubmit} className={centered ? 'w-full' : 'shrink-0 px-5 pb-4 pt-2'}>
      <div className="group relative mx-auto flex w-full max-w-[720px] items-center gap-2 rounded-full bg-card py-2 pl-5 pr-2 shadow-bar">
        {/* The glow is its own layer that only fades in: animating a box-shadow repaints the area every frame, which lagged; an opacity change does not. */}
        <span aria-hidden className="pointer-events-none absolute inset-0 rounded-full opacity-0 shadow-[0_0_0_3px_var(--accent-soft),0_0_0_1px_var(--accent)] transition-opacity duration-200 ease-out will-change-[opacity] group-focus-within:opacity-100" />
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          maxLength={2000}
          className="min-w-0 flex-1 bg-transparent text-sm text-label placeholder-label-3 outline-none disabled:cursor-not-allowed disabled:opacity-60"
        />
        {streaming ? (
          <button type="button" onClick={onStop} aria-label="Stop" title="Stop the answer" className={`${ROUND} bg-label text-bg hover:opacity-80`}>
            <Square className="h-3 w-3 fill-current" />
          </button>
        ) : (
          <button type="submit" disabled={disabled || !value.trim()} aria-label="Ask" className={`${ROUND} bg-accent text-white hover:brightness-110 disabled:cursor-not-allowed disabled:bg-fill-strong disabled:text-label-3`}>
            <ArrowUp className="h-4 w-4" strokeWidth={2.25} />
          </button>
        )}
      </div>
    </form>
  )
}

export default MessageInput
