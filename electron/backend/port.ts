// A free port on 127.0.0.1 (task 8.7). Ask the operating system for port 0, read what it gave, and give it back.
// There is a short window before the backend takes the port; the caller starts the backend at once and a failed start is reported, not hidden.
import net from 'node:net'

type ServerLike = {
  once(event: string, listener: (...args: never[]) => void): ServerLike
  listen(port: number, host: string, callback: () => void): ServerLike
  address(): { port: number } | string | null
  close(callback?: () => void): ServerLike
}

export function findFreePort(createServer: () => ServerLike = () => net.createServer() as unknown as ServerLike): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', ((error: Error) => reject(error)) as never)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') {
        server.close(() => reject(new Error('could not read the port the system gave')))
        return
      }
      server.close(() => resolve(address.port))
    })
  })
}
