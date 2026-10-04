// The process that runs the backend (task 8.7). It must never be left behind. `stop()` closes the backend's stdin (the backend watches it), asks politely with
// SIGTERM (uvicorn then shuts down cleanly and running index jobs are cancelled between files), and kills it for good after a grace period. If THIS process
// dies in a way that runs no code at all (a crash, a kill -9), the operating system closes the pipe and the backend's own watcher stops it.
// What the backend prints is kept (the last lines, the token removed) so that a failed start can say why.
import { EventEmitter } from 'node:events'

const TAIL_LINES = 20

export type ExitInfo = { expected: boolean, code: number | null, signal: string | null, tail: string[] }

type StreamLike = { on(event: string, listener: (...args: never[]) => void): unknown, end?: () => void }
type ChildLike = {
  stdin: StreamLike
  stdout: StreamLike
  stderr: StreamLike
  pid?: number
  kill(signal: string): boolean
  on(event: string, listener: (...args: never[]) => void): unknown
}

export type ProcessOptions = {
  command: string
  args: string[]
  cwd: string
  env: Record<string, string>
  redact: string[]
  spawn: (command: string, args: string[], options: Record<string, unknown>) => ChildLike
  setTimeoutFn?: (fn: () => void, ms: number) => unknown
  clearTimeoutFn?: (timer: unknown) => void
  killGraceMs?: number
}

export class BackendProcess extends EventEmitter {
  private options: ProcessOptions
  private child: ChildLike | null = null
  private alive = false
  private tail: string[] = []
  private partial: Record<string, string> = { stdout: '', stderr: '' }
  private expectedStop = false
  private stopping: Promise<void> | null = null
  private resolveStopping: (() => void) | null = null
  private killTimer: unknown = null

  constructor(options: ProcessOptions) {
    super()
    this.options = options
  }

  get pid(): number | undefined {
    return this.child?.pid
  }

  isAlive(): boolean {
    return this.alive
  }

  start(): void {
    if (this.alive) return
    const { command, args, cwd, env, spawn } = this.options
    this.tail = []
    this.partial = { stdout: '', stderr: '' }
    this.expectedStop = false
    // stdin stays open on purpose: its closing is how the backend learns that this app has died. Never through a shell.
    const child = spawn(command, args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] })
    this.child = child
    this.alive = true
    let finished = false
    const finish = (code: number | null, signal: string | null) => {
      if (finished) return
      finished = true
      this.alive = false
      if (this.killTimer !== null) (this.options.clearTimeoutFn ?? clearTimeout)(this.killTimer as never)
      this.killTimer = null
      for (const name of ['stdout', 'stderr']) this.keep(this.partial[name])
      this.partial = { stdout: '', stderr: '' }
      const info: ExitInfo = { expected: this.expectedStop, code, signal, tail: [...this.tail] }
      this.child = null
      const done = this.resolveStopping
      this.stopping = null
      this.resolveStopping = null
      this.emit('exit', info)
      done?.()
    }
    const noop = () => {}
    child.stdin.on('error', noop)                               // a write to a process that already ended is not our problem
    for (const name of ['stdout', 'stderr'] as const) {
      child[name].on('error', noop)
      child[name].on('data', ((chunk: Buffer) => this.collect(name, chunk.toString('utf8'))) as never)
    }
    child.on('exit', ((code: number | null, signal: string | null) => finish(code, signal)) as never)
    child.on('error', ((error: Error) => {
      this.keep(`could not start: ${error.message}`)
      finish(null, null)
    }) as never)
  }

  stop(): Promise<void> {
    if (!this.alive || this.child === null) return Promise.resolve()
    if (this.stopping) return this.stopping
    const child = this.child
    this.expectedStop = true
    this.stopping = new Promise<void>((resolve) => { this.resolveStopping = resolve })
    child.stdin.end?.()
    child.kill('SIGTERM')
    this.killTimer = (this.options.setTimeoutFn ?? setTimeout)(() => { child.kill('SIGKILL') }, this.options.killGraceMs ?? 5000)
    return this.stopping
  }

  /** A synchronous SIGKILL, for the last moment (the app is going away and cannot wait). */
  killNow(): void {
    this.expectedStop = true
    this.child?.kill('SIGKILL')
  }

  private collect(name: string, text: string): void {
    const lines = (this.partial[name] + text).split('\n')
    this.partial[name] = lines.pop() ?? ''
    for (const line of lines) this.keep(line)
  }

  private keep(line: string): void {
    if (line === '') return
    let clean = line
    for (const secret of this.options.redact) if (secret) clean = clean.split(secret).join('[token]')
    this.tail.push(clean)
    if (this.tail.length > TAIL_LINES) this.tail.shift()
  }
}
