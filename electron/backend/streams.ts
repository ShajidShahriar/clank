// The door for answers that are written live (step 1.3). The window names an id, a path of exactly one shape and a JSON body; this file (in the main process, which holds
// the token) makes the request, reads the backend's NDJSON stream, and hands the events to the window that asked, and only to it. Erasable TypeScript only.
//
// What it guarantees (tests: streams.test.ts, streams.live.test.ts):
// - the address, port and token are never the window's to choose and never reach it; a refusal before the stream starts is an ordinary { ok, status, body } result;
// - everything the backend sends is untrusted: lines and the whole stream are size-limited, every line must be a valid event, a silent backend is given up on;
// - a stream ends with exactly ONE last event (done, error or cancelled) and nothing after it; the window's Stop aborts the request, which is what tells the backend
//   (and through it the model) to stop;
// - at most about 25 messages a second per stream: the first event after a quiet moment goes out at once, the rest are batched (text joined, thinking counts reduced);
// - only the window that asked receives events or can stop; closing a window ends its streams quietly; a backend that stops ends them all with an error event;
// - the number of streams is limited.
import { buildBackendRequest } from './request.ts'
import { LineReader, StreamFormatError, StreamLimitError, coalesce, isTerminal, parseEvent, type ReaderLimits, type StreamEvent } from './stream-events.ts'

export const FLUSH_MS = 40                         // 25 messages a second
export const HEADERS_TIMEOUT_MS = 300_000          // the backend searches, then opens the model's connection (a model's own timeout is up to 180 s)
export const IDLE_TIMEOUT_MS = 300_000             // no byte at all for this long: the backend is given up on (also longer than any model timeout)
export const MAX_PER_OWNER = 4
export const MAX_TOTAL = 8
const PATH = /^\/projects\/[1-9][0-9]{0,9}\/answer\/stream$/
const ID = /^[A-Za-z0-9_-]{8,64}$/
const MAX_REFUSAL_BYTES = 65_536

export type Owner = { id: number, send(message: { id: string, events: StreamEvent[] }): void }
export type StreamTarget = { port: number, token: string }
export type StartRequest = { id: string, path: string, body?: unknown }
type Failure = { ok: false, status: number, body: { error: { code: string, message: string } } }
export type StartResult = { ok: true, status: 200, body: { id: string } } | Failure

type Handle = unknown
type FetchLike = (url: string, init: Record<string, unknown> & { signal: AbortSignal }) => Promise<{ status: number, headers: { get(name: string): string | null }, body: ReadableStream<Uint8Array> | null }>

export type StreamDeps = {
  target: () => StreamTarget | null
  fetchFn?: FetchLike
  now?: () => number
  setTimer?: (fn: () => void, ms: number) => Handle
  clearTimer?: (handle: Handle) => void
  limits?: ReaderLimits
}

const NOT_ALLOWED = { code: 'bad_request', message: 'The request was not allowed.' }
const NOT_RUNNING = { code: 'backend_unavailable', message: 'The Clank backend is not running.' }
const NOT_REACHABLE = { code: 'backend_unavailable', message: 'The Clank backend is not reachable.' }
const STOPPED = { code: 'backend_unavailable', message: 'The Clank backend stopped.' }
const STOPPED_EARLY = { code: 'backend_unavailable', message: 'The Clank backend stopped answering before the answer was complete.' }
const TIMEOUT = { code: 'backend_timeout', message: 'The Clank backend took too long to answer.' }
const UNREADABLE = { code: 'bad_response', message: 'Clank got a reply it could not read. The app and its backend may be out of date.' }
const TOO_MANY = { code: 'too_many_streams', message: 'Too many answers are being written at once.' }
const CANCELLED = { code: 'cancelled', message: 'The answer was stopped.' }

type Why = 'stopped' | 'closed' | 'backend' | 'timeout'
type Entry = {
  key: string
  id: string
  owner: Owner
  controller: AbortController
  state: 'connecting' | 'open'
  ended: boolean
  why: Why | null
  queue: StreamEvent[]
  lastFlush: number
  flushTimer: Handle | null
  idleTimer: Handle | null
  headersTimer: Handle | null
  reader: ReadableStreamDefaultReader<Uint8Array> | null
}

const fail = (info: { code: string, message: string }, status = 0): Failure => ({ ok: false, status, body: { error: info } })
const errorEvent = (info: { code: string, message: string }): StreamEvent => ({ type: 'error', error: { ...info, status: 0 } })

export class StreamManager {
  private deps: StreamDeps
  private streams = new Map<string, Entry>()
  private fetchFn: FetchLike
  private now: () => number
  private setTimer: (fn: () => void, ms: number) => Handle
  private clearTimer: (handle: Handle) => void

  constructor(deps: StreamDeps) {
    this.deps = deps
    this.fetchFn = deps.fetchFn ?? (fetch as unknown as FetchLike)
    this.now = deps.now ?? Date.now
    this.setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms))
    this.clearTimer = deps.clearTimer ?? ((handle) => clearTimeout(handle as never))
  }

  get active(): number {
    return this.streams.size
  }

  async start(owner: Owner, request: StartRequest): Promise<StartResult> {
    const { id, path, body } = request ?? ({} as StartRequest)
    if (typeof id !== 'string' || !ID.test(id) || typeof path !== 'string' || !PATH.test(path)) return fail(NOT_ALLOWED)
    const key = `${owner.id}:${id}`
    if (this.streams.has(key)) return fail(NOT_ALLOWED)
    const target = this.deps.target()
    let built: ReturnType<typeof buildBackendRequest>
    try {
      built = buildBackendRequest({ port: target?.port ?? 1, token: target?.token ?? 'x'.repeat(32), method: 'POST', path, body })
    } catch {
      return fail(NOT_ALLOWED)
    }
    if (target === null) return fail(NOT_RUNNING)
    if (this.streams.size >= MAX_TOTAL || [...this.streams.values()].filter((s) => s.owner.id === owner.id).length >= MAX_PER_OWNER) return fail(TOO_MANY)

    const rec: Entry = {
      key, id, owner, controller: new AbortController(), state: 'connecting', ended: false, why: null, queue: [], lastFlush: -Infinity,
      flushTimer: null, idleTimer: null, headersTimer: null, reader: null,
    }
    this.streams.set(key, rec)
    rec.headersTimer = this.setTimer(() => { rec.why = 'timeout'; rec.controller.abort() }, HEADERS_TIMEOUT_MS)
    const headers = { ...built.init.headers, accept: 'application/x-ndjson' }

    let response: Awaited<ReturnType<FetchLike>>
    try {
      response = await this.fetchFn(built.url, { ...built.init, headers, signal: rec.controller.signal })
    } catch {
      return this.failedStart(rec)
    }
    this.clear(rec.headersTimer)
    rec.headersTimer = null
    if (rec.ended) {
      void response.body?.cancel().catch(() => {})
      return this.failedStart(rec)
    }
    if (response.status !== 200) {
      const refusal = await this.readRefusal(response)
      this.drop(rec)
      return refusal
    }
    const type = (response.headers.get('content-type') ?? '').toLowerCase()
    if (!type.includes('application/x-ndjson') || response.body === null) {
      void response.body?.cancel().catch(() => {})
      this.drop(rec)
      return fail(UNREADABLE)
    }
    rec.state = 'open'
    this.resetIdle(rec)
    void this.pump(rec, response.body)
    return { ok: true, status: 200, body: { id } }
  }

  /** The window's Stop. Only the window that started the stream can stop it. Returns whether a stream was stopped. */
  stop(ownerId: number, id: string): boolean {
    const rec = this.streams.get(`${ownerId}:${id}`)
    if (!rec || rec.ended) return false
    rec.why = 'stopped'
    if (rec.state === 'connecting') this.drop(rec)
    else {
      rec.queue = []                                 // the half-written batch is of no use to a window that asked to stop
      this.end(rec, [{ type: 'cancelled' }])
    }
    return true
  }

  /** The window is gone: its streams end and nothing is sent to it. */
  closeOwner(ownerId: number): void {
    for (const rec of [...this.streams.values()]) {
      if (rec.owner.id !== ownerId) continue
      rec.why = 'closed'
      this.drop(rec)
    }
  }

  /** The backend stopped, crashed or is restarting: every open stream ends with an error event, every start still waiting fails. */
  abortAll(): void {
    for (const rec of [...this.streams.values()]) {
      rec.why = 'backend'
      if (rec.state === 'open') this.end(rec, [errorEvent(STOPPED)])
      else this.drop(rec)
    }
  }

  // ---- reading the stream

  private async pump(rec: Entry, body: ReadableStream<Uint8Array>): Promise<void> {
    const reader = body.getReader()
    rec.reader = reader
    const lines = new LineReader(this.deps.limits)
    try {
      for (;;) {
        const { value, done } = await reader.read()
        if (rec.ended) return
        if (done) {
          for (const line of lines.finish()) {
            this.handle(rec, line)
            if (rec.ended) return
          }
          this.end(rec, [errorEvent(STOPPED_EARLY)])
          return
        }
        this.resetIdle(rec)
        for (const line of lines.push(value)) {
          this.handle(rec, line)
          if (rec.ended) return
        }
      }
    } catch (problem) {
      if (rec.ended) return
      this.end(rec, [errorEvent(problem instanceof StreamLimitError || problem instanceof StreamFormatError ? UNREADABLE : STOPPED_EARLY)])
    }
  }

  private handle(rec: Entry, line: string): void {
    const event = parseEvent(line)
    if (isTerminal(event)) this.end(rec, [event])
    else this.enqueue(rec, event)
  }

  private enqueue(rec: Entry, event: StreamEvent): void {
    rec.queue.push(event)
    const since = this.now() - rec.lastFlush
    if (since >= FLUSH_MS) this.deliver(rec)
    else if (rec.flushTimer === null) {
      rec.flushTimer = this.setTimer(() => { rec.flushTimer = null; this.deliver(rec) }, FLUSH_MS - since)
    }
  }

  /** Send what is waiting, batched. Returns false (and ends the stream quietly) if the window cannot be written to. */
  private deliver(rec: Entry): boolean {
    if (rec.flushTimer !== null) {
      this.clear(rec.flushTimer)
      rec.flushTimer = null
    }
    if (rec.queue.length === 0) return true
    const events = coalesce(rec.queue)
    rec.queue = []
    rec.lastFlush = this.now()
    try {
      rec.owner.send({ id: rec.id, events })
      return true
    } catch {
      this.drop(rec)                                   // the window is gone (destroyed): nothing to tell anyone
      return false
    }
  }

  // ---- ending

  /**
   * End the stream with these last events (after whatever was waiting). The stream is marked ended BEFORE anything is delivered: a window that answers an event at once
   * (a Stop, a close) finds a stream that has ended, and cannot add a second last event.
   */
  private end(rec: Entry, last: StreamEvent[]): void {
    if (rec.ended) return
    rec.ended = true
    rec.queue.push(...last)
    this.deliver(rec)
    this.release(rec)
  }

  /** End the stream, say nothing. */
  private drop(rec: Entry): void {
    rec.ended = true
    this.release(rec)
  }

  /** Let go of everything the stream holds (safe to do more than once). */
  private release(rec: Entry): void {
    for (const handle of [rec.flushTimer, rec.idleTimer, rec.headersTimer]) if (handle !== null) this.clear(handle)
    rec.flushTimer = rec.idleTimer = rec.headersTimer = null
    rec.queue = []
    this.streams.delete(rec.key)
    try { rec.controller.abort() } catch { /* already aborted */ }
    void rec.reader?.cancel().catch(() => {})
  }

  private resetIdle(rec: Entry): void {
    if (rec.ended) return
    if (rec.idleTimer !== null) this.clear(rec.idleTimer)
    rec.idleTimer = this.setTimer(() => this.end(rec, [errorEvent(TIMEOUT)]), IDLE_TIMEOUT_MS)
  }

  private clear(handle: Handle): void {
    this.clearTimer(handle)
  }

  // ---- answers to a start that did not become a stream

  private failedStart(rec: Entry): Failure {
    const why = rec.why
    this.drop(rec)
    if (why === 'stopped' || why === 'closed') return fail(CANCELLED)
    if (why === 'backend') return fail(STOPPED)
    if (why === 'timeout') return fail(TIMEOUT)
    return fail(NOT_REACHABLE)                          // (the real error could hold the port or the token: it is not passed on)
  }

  private async readRefusal(response: Awaited<ReturnType<FetchLike>>): Promise<Failure> {
    let text = ''
    try {
      const reader = response.body?.getReader()
      const decoder = new TextDecoder()
      let size = 0
      while (reader) {
        const { value, done } = await reader.read()
        if (done) break
        size += value.length
        text += decoder.decode(value, { stream: true })
        if (size > MAX_REFUSAL_BYTES) {
          void reader.cancel().catch(() => {})
          text = ''
          break
        }
      }
    } catch {
      text = ''
    }
    try {
      const parsed = JSON.parse(text) as { error?: { code?: unknown, message?: unknown } }
      if (parsed && typeof parsed.error === 'object' && parsed.error !== null && typeof parsed.error.code === 'string' && typeof parsed.error.message === 'string') {
        return { ok: false, status: response.status, body: parsed as Failure['body'] }
      }
    } catch { /* not JSON */ }
    return fail(UNREADABLE, response.status)
  }
}
