// The whole life of the backend as the app sees it (task 8.7): starting -> ready -> (crashed | stopped), or starting -> failed.
// Promises: every request carries the token; nothing is sent while the backend is not ready; a failed start, a stop and a restart never leave a process behind;
// a restart uses a NEW port and a NEW token; a status never holds the token.
import { backendCommand, buildBackendEnv, dataDirFor, makeToken as defaultMakeToken } from './env.ts'
import { waitForHealth } from './health.ts'
import { findFreePort } from './port.ts'
import { BackendProcess, type ExitInfo } from './process.ts'
import { callBackend, type CallResult } from './request.ts'

export type BackendState = 'starting' | 'ready' | 'crashed' | 'failed' | 'stopped'
export type BackendStatus = { state: BackendState, message: string, detail: string[] }

const MESSAGES: Record<BackendState, string> = {
  starting: 'Starting the Clank backend.',
  ready: 'The Clank backend is running.',
  crashed: 'The Clank backend stopped unexpectedly.',
  failed: 'The Clank backend could not start.',
  stopped: 'The Clank backend is stopped.',
}

type AnyFetch = (url: string, init: never) => Promise<{ status: number, json(): Promise<unknown>, text(): Promise<string> }>

export type ServiceDeps = {
  userDataPath: string
  appRoot: string
  resourcesPath: string
  packaged: boolean
  platform: string
  baseEnv: Record<string, string | undefined>
  spawn: ConstructorParameters<typeof BackendProcess>[0]['spawn']
  findPort?: () => Promise<number>
  makeToken?: () => string
  fetchFn?: AnyFetch
  now?: () => number
  sleep?: (ms: number) => Promise<void>
  healthTimeoutMs?: number
  healthIntervalMs?: number
  killGraceMs?: number
  requestTimeoutMs?: number
}

type Run = { port: number, token: string, proc: BackendProcess | null, ready: boolean, exitInfo: ExitInfo | null }

export class BackendService {
  private deps: ServiceDeps
  private run: Run | null = null
  private current: BackendStatus = { state: 'stopped', message: MESSAGES.stopped, detail: [] }
  private listeners = new Set<(status: BackendStatus) => void>()
  private startPromise: Promise<void> | null = null

  constructor(deps: ServiceDeps) {
    this.deps = deps
  }

  status(): BackendStatus {
    return { ...this.current, detail: [...this.current.detail] }
  }

  onStatus(listener: (status: BackendStatus) => void): () => void {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  /** Resolves when the backend is ready or the start has failed (see status()); it never rejects. */
  start(): Promise<void> {
    if (this.startPromise) return this.startPromise
    if (this.current.state === 'ready' || this.current.state === 'starting') return Promise.resolve()
    const promise: Promise<void> = this.doStart().finally(() => { if (this.startPromise === promise) this.startPromise = null })
    this.startPromise = promise
    return promise
  }

  async stop(): Promise<void> {
    const run = this.run
    this.run = null                                          // from here on, events of this run are ignored and requests are refused
    this.setStatus('stopped')
    await run?.proc?.stop()
  }

  async restart(): Promise<void> {
    await this.stop()
    await this.start()
  }

  request(method: string, path: string, body?: unknown): Promise<CallResult> {
    const run = this.run
    if (this.current.state !== 'ready' || run === null || !run.ready) {
      return Promise.resolve({ ok: false, status: 0, body: { error: { code: 'backend_unavailable', message: 'The Clank backend is not running.' } } })
    }
    return callBackend({ port: run.port, token: run.token, method, path, body, fetchFn: this.deps.fetchFn as never, timeoutMs: this.deps.requestTimeoutMs })
  }

  /** A synchronous SIGKILL, for the last moment (the app is going away and cannot wait). */
  killNow(): void {
    this.run?.proc?.killNow()
  }

  private async doStart(): Promise<void> {
    const d = this.deps
    const run: Run = { port: 0, token: '', proc: null, ready: false, exitInfo: null }
    this.run = run
    this.setStatus('starting')
    try {
      run.port = await (d.findPort ?? findFreePort)()
      if (this.run !== run) return                           // stopped while a port was being found
      run.token = (d.makeToken ?? defaultMakeToken)()
      const env = buildBackendEnv({ baseEnv: d.baseEnv, dataDir: dataDirFor(d.userDataPath), token: run.token })
      const command = backendCommand({ packaged: d.packaged, appRoot: d.appRoot, resourcesPath: d.resourcesPath, platform: d.platform, port: run.port })
      const proc = new BackendProcess({ ...command, env, redact: [run.token], spawn: d.spawn, killGraceMs: d.killGraceMs })
      run.proc = proc
      proc.on('exit', (info: ExitInfo) => this.onExit(run, info))
      proc.start()
      await waitForHealth({
        url: `http://127.0.0.1:${run.port}/health`, token: run.token, fetchFn: d.fetchFn as never, now: d.now, sleep: d.sleep,
        timeoutMs: d.healthTimeoutMs, intervalMs: d.healthIntervalMs, isAlive: () => proc.isAlive() && this.run === run,
      })
      if (this.run !== run) return
      run.ready = true
      this.setStatus('ready')
    } catch (error) {
      await run.proc?.stop()                                 // never leave the process behind (its exit fills run.exitInfo)
      if (this.run !== run) return                           // a stop or a restart took over while we waited: this is not a failure
      this.run = null
      const reason = String((error as Error).message ?? error)
      this.setStatus('failed', [this.clean(reason, run), ...(run.exitInfo?.tail ?? [])])
    }
  }

  private onExit(run: Run, info: ExitInfo): void {
    if (this.run !== run) return                             // an old run, or one that was stopped on purpose
    run.exitInfo = info
    if (run.ready && !info.expected) {
      this.run = null
      const how = info.signal ? `signal ${info.signal}` : `exit code ${info.code}`
      this.setStatus('crashed', [...info.tail, how])
    }
  }

  private clean(text: string, run: Run): string {
    return run.token ? text.split(run.token).join('[token]') : text
  }

  private setStatus(state: BackendState, detail: string[] = []): void {
    const next: BackendStatus = { state, message: MESSAGES[state], detail }
    if (JSON.stringify(next) === JSON.stringify(this.current)) return
    this.current = next
    for (const listener of [...this.listeners]) {
      try { listener(this.status()) } catch { /* one bad listener must not stop the others or the service */ }
    }
  }
}
