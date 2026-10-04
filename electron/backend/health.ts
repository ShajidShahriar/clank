// Wait for the backend to answer /health (task 8.7). Every request needs the token, this one too. It gives up when the process has died or the time is up,
// and the token is never put in an error.

type FetchLike = (url: string, init: { method: string, headers: Record<string, string> }) => Promise<{ status: number, json(): Promise<unknown> }>

export type WaitInput = {
  url: string
  token: string
  fetchFn?: FetchLike
  isAlive: () => boolean
  now?: () => number
  sleep?: (ms: number) => Promise<void>
  timeoutMs?: number
  intervalMs?: number
}

export async function waitForHealth({
  url, token, fetchFn = fetch as unknown as FetchLike, isAlive, now = Date.now,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)), timeoutMs = 30000, intervalMs = 250,
}: WaitInput): Promise<void> {
  const started = now()
  let lastProblem = 'no answer yet'
  while (true) {
    if (!isAlive()) throw new Error(`the backend stopped before it was ready (${lastProblem})`)
    try {
      const reply = await fetchFn(url, { method: 'GET', headers: { 'x-clank-token': token } })
      if (reply.status === 200) {
        const body = await reply.json() as { status?: unknown }
        if (body && body.status === 'ok') return
        lastProblem = 'the answer was not a health report'
      } else {
        lastProblem = `status ${reply.status}`
      }
    } catch (error) {
      lastProblem = String((error as Error).message ?? error).split(token).join('[token]')
    }
    if (now() - started >= timeoutMs) {
      throw new Error(`the backend did not become ready within ${Math.round(timeoutMs / 1000)} seconds (${lastProblem})`)
    }
    await sleep(intervalMs)
  }
}
