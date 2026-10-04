// What the backend is started with (task 8.7). The token and the data folder travel in the ENVIRONMENT: a command line is visible to every process on the
// machine through `ps`. Written with erasable TypeScript only (no enums, no parameter properties) so that `node --test` can run it without a build step.
import { randomBytes as nodeRandomBytes } from 'node:crypto'
import path from 'node:path'

const MIN_TOKEN_CHARS = 32

/** A new random token for one launch: 32 random bytes as base64url, 43 URL-safe characters. */
export function makeToken(randomBytes: (n: number) => Buffer = nodeRandomBytes): string {
  return randomBytes(32).toString('base64url')
}

/** The backend keeps its database, vectors and locks in a `backend` folder inside the app's user-data folder (which also holds Chromium's own files). */
export function dataDirFor(userDataPath: string): string {
  if (!userDataPath || !path.isAbsolute(userDataPath)) throw new Error('the user-data path must be absolute')
  return path.join(userDataPath, 'backend')
}

export type BackendEnvInput = {
  baseEnv: Record<string, string | undefined>
  dataDir: string
  token: string
}

export function buildBackendEnv({ baseEnv, dataDir, token }: BackendEnvInput): Record<string, string> {
  if (!path.isAbsolute(dataDir)) throw new Error('the data folder must be an absolute path')
  if (token.length < MIN_TOKEN_CHARS) throw new Error(`the token must be at least ${MIN_TOKEN_CHARS} characters`)   // the value is never put in a message
  const env: Record<string, string> = {}
  for (const [name, value] of Object.entries(baseEnv)) {
    if (value !== undefined && !name.startsWith('CLANK_')) env[name] = value        // nothing of the parent's own CLANK_* settings leaks through
  }
  env.CLANK_DATA_DIR = dataDir
  env.CLANK_API_TOKEN = token
  env.CLANK_ALLOWED_ORIGINS = ''                                                    // the window talks to the backend through this process, never directly
  env.PYTHONUNBUFFERED = '1'
  return env
}

export type CommandInput = {
  packaged: boolean
  appRoot: string
  resourcesPath: string
  platform: string
  port: number
}

export type Command = { command: string, args: string[], cwd: string }

/** How to start the backend. There is no host argument: it listens on 127.0.0.1 and nowhere else by itself. */
export function backendCommand({ packaged, appRoot, resourcesPath, platform, port }: CommandInput): Command {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(`not a valid port: ${port}`)
  const tail = ['--port', String(port), '--exit-when-stdin-closes']
  if (!packaged) {
    return { command: 'uv', args: ['run', 'python', 'run.py', ...tail], cwd: path.join(appRoot, 'backend') }
  }
  const exe = platform === 'win32' ? 'clank-backend.exe' : 'clank-backend'
  return { command: path.posix.join(resourcesPath.replaceAll('\\', '/'), 'backend', exe), args: tail, cwd: resourcesPath }
}
