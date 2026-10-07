// Reading the backend's answer stream safely (step 1.3). Pure: no network, no timers, so all of it is tested without either. Erasable TypeScript only (no enums, no
// parameter properties) so that `node --test` can run it without a build step.
//
// The backend sends NDJSON: one JSON object per line. Everything it sends is untrusted input here: a line is cut out of the bytes with a hard limit on memory, must be valid
// text, must be a JSON object, and must have one of the six `type`s the backend uses. What the fields inside hold is the window's business (src/api/client.ts checks them).

export const EVENT_TYPES = ['start', 'stage', 'thinking', 'delta', 'done', 'error'] as const
const TERMINAL = new Set(['done', 'error', 'cancelled'])           // `cancelled` is the app's own: the backend never sends it

export type StreamEvent = { type: string, [field: string]: unknown }

export class StreamFormatError extends Error {
  code = 'bad_response'
}

export class StreamLimitError extends Error {
  code: 'line_too_long' | 'too_big'
  constructor(code: 'line_too_long' | 'too_big') {
    super(code === 'line_too_long' ? 'a line of the answer stream is far too long' : 'the answer stream is far too big')
    this.code = code
  }
}

export const MAX_LINE_BYTES = 1_048_576            // one event (the last one carries the whole answer and its sources) is a few KB
export const MAX_TOTAL_BYTES = 8_388_608

export type ReaderLimits = { maxLineBytes?: number, maxTotalBytes?: number }

/** Cuts bytes into lines. Works on BYTES, so a character cut across two chunks is never a problem; memory is bounded by the limits. */
export class LineReader {
  private parts: Uint8Array[] = []
  private length = 0
  private total = 0
  private maxLine: number
  private maxTotal: number
  private decoder = new TextDecoder('utf-8', { fatal: true })

  constructor(limits: ReaderLimits = {}) {
    this.maxLine = limits.maxLineBytes ?? MAX_LINE_BYTES
    this.maxTotal = limits.maxTotalBytes ?? MAX_TOTAL_BYTES
  }

  push(chunk: Uint8Array): string[] {
    this.total += chunk.length
    if (this.total > this.maxTotal) throw new StreamLimitError('too_big')
    const lines: string[] = []
    let start = 0
    for (let i = 0; i < chunk.length; i++) {
      if (chunk[i] !== 0x0a) continue
      this.add(chunk.subarray(start, i))
      start = i + 1
      const line = this.take()
      if (line !== null) lines.push(line)
    }
    this.add(chunk.subarray(start))
    return lines
  }

  /** The end of the stream: a last line that had no newline comes out now (once). */
  finish(): string[] {
    const line = this.take()
    return line === null ? [] : [line]
  }

  private add(part: Uint8Array): void {
    if (part.length === 0) return
    this.parts.push(part)
    this.length += part.length
    if (this.length > this.maxLine) throw new StreamLimitError('line_too_long')       // refused before the end of the line arrives: a line with no end cannot fill the memory
  }

  private take(): string | null {
    const bytes = new Uint8Array(this.length)
    let at = 0
    for (const part of this.parts) {
      bytes.set(part, at)
      at += part.length
    }
    this.parts = []
    this.length = 0
    let text: string
    try {
      text = this.decoder.decode(bytes)
    } catch {
      throw new StreamFormatError('the answer stream is not text')
    }
    if (text.endsWith('\r')) text = text.slice(0, -1)
    return text.trim() === '' ? null : text
  }
}

/** One line -> one event. Anything that is not a JSON object with one of the six types is refused. */
export function parseEvent(line: string): StreamEvent {
  let value: unknown
  try {
    value = JSON.parse(line)
  } catch {
    throw new StreamFormatError('an event of the answer stream is not JSON')
  }
  if (typeof value !== 'object' || value === null) throw new StreamFormatError('an event of the answer stream is not an object')       // (an array has no `type`: it fails the next check)
  const type = (value as { type?: unknown }).type
  if (typeof type !== 'string' || !(EVENT_TYPES as readonly string[]).includes(type)) throw new StreamFormatError('an event of the answer stream has an unknown type')
  return value as StreamEvent
}

export function isTerminal(event: { type: string }): boolean {
  return TERMINAL.has(event.type)
}

/** Fewer, bigger events for the window: neighbouring text pieces become one, neighbouring thinking counts keep the latest. Nothing else changes or moves. */
export function coalesce(events: StreamEvent[]): StreamEvent[] {
  const out: StreamEvent[] = []
  for (const event of events) {
    const last = out[out.length - 1]
    if (event.type === 'delta' && last?.type === 'delta') {
      out[out.length - 1] = { type: 'delta', text: String(last.text ?? '') + String(event.text ?? '') }
    } else if (event.type === 'thinking' && last?.type === 'thinking') {
      out[out.length - 1] = event
    } else {
      out.push(event)
    }
  }
  return out
}
