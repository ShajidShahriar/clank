// The only way the window reaches the backend (task 8.7): through the main process, which holds the token. The window may only name a method, a path and a JSON
// body. The address, the port and the token are never its to choose, the path must have a strict shape (no query, no dots, no encoded characters, no second
// slash), a redirect is never followed (the token must not travel anywhere else), and what goes back is the parsed answer, never the request.

const PATH = /^\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*$/
const MAX_BODY_CHARS = 65536
const DEFAULT_TIMEOUT_MS = 120000

export type BackendRequestInput = {
  port: number
  token: string
  method: string
  path: string
  body?: unknown
}

export type BuiltRequest = {
  url: string
  init: { method: string, headers: Record<string, string>, body?: string, redirect: 'error' }
}

export function buildBackendRequest({ port, token, method, path, body }: BackendRequestInput): BuiltRequest {
  if (method !== 'GET' && method !== 'POST' && method !== 'PUT' && method !== 'DELETE') throw new Error('the method is not allowed')
  if (typeof path !== 'string' || !PATH.test(path)) throw new Error('the path is not allowed')
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('the port is not valid')
  const headers: Record<string, string> = { 'x-clank-token': token }
  let encoded: string | undefined
  if (body !== undefined) {
    if (method !== 'POST' && method !== 'PUT') throw new Error('only a POST or a PUT can have a body')
    const plain = typeof body === 'object' && body !== null && !Array.isArray(body)
      && (Object.getPrototypeOf(body) === Object.prototype || Object.getPrototypeOf(body) === null)
    if (!plain) throw new Error('the body must be a plain object')
    try {
      encoded = JSON.stringify(body)
    } catch {
      throw new Error('the body cannot be turned into JSON')
    }
    if (encoded.length > MAX_BODY_CHARS) throw new Error('the body is too big')
    headers['content-type'] = 'application/json'
  }
  return { url: `http://127.0.0.1:${port}${path}`, init: { method, headers, body: encoded, redirect: 'error' } }
}

type FetchLike = (url: string, init: BuiltRequest['init'] & { signal: AbortSignal }) => Promise<{ status: number, text(): Promise<string> }>

export type CallInput = BackendRequestInput & { fetchFn?: FetchLike, timeoutMs?: number }

export type CallResult = { ok: boolean, status: number, body: unknown }

function failure(status: number, code: string, message: string): CallResult {
  return { ok: false, status, body: { error: { code, message } } }
}

export async function callBackend({ fetchFn = fetch as unknown as FetchLike, timeoutMs = DEFAULT_TIMEOUT_MS, ...input }: CallInput): Promise<CallResult> {
  let built: BuiltRequest
  try {
    built = buildBackendRequest(input)
  } catch {
    return failure(0, 'bad_request', 'The request was not allowed.')
  }
  try {
    const reply = await fetchFn(built.url, { ...built.init, signal: AbortSignal.timeout(timeoutMs) })
    let body: unknown = null
    try {
      body = JSON.parse(await reply.text())
    } catch {
      body = null
    }
    return { ok: reply.status >= 200 && reply.status < 300, status: reply.status, body }
  } catch (error) {
    const name = (error as Error)?.name
    if (name === 'TimeoutError' || name === 'AbortError') return failure(0, 'backend_timeout', 'The Clank backend took too long to answer.')
    return failure(0, 'backend_unavailable', 'The Clank backend is not reachable.')           // the real error is not passed on: it could hold the token or a path
  }
}
