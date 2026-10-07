// The window's typed wrapper around the backend bridge (`window.clankBackend`, see electron/preload.ts). Everything returns a Result and nothing throws:
// the data, or an error {code, message, status} written for people. A reply with the wrong shape is "bad_response" and is never half-used.
// Written with erasable TypeScript only (no enums, no parameter properties) so that `node --test` can run it without a build step.
import type { Answer, ApiError, Conversation, ConversationSummary, Health, IndexStatus, LlmSettings, LlmTest, Project, Result, Source } from './types.ts'

export type BridgeReply = { ok: boolean, status: number, body: unknown }
export type Bridge = {
  request(method: 'GET' | 'POST' | 'DELETE', path: string, body?: object): Promise<BridgeReply>
  pickFolder(): Promise<BridgeReply>
  saveLlmSettings(config: object, apiKey?: string | null): Promise<BridgeReply>     // the ONLY door for a key: the desktop app encrypts it and pushes it to the backend
}

const INDEX_STATES = ['idle', 'running', 'cancelling', 'done', 'stopped', 'cancelled', 'failed']

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isStr = (v: unknown): v is string => typeof v === 'string'
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v)
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isBool = (v: unknown): v is boolean => typeof v === 'boolean'
const orNull = (check: (v: unknown) => boolean) => (v: unknown) => v === null || check(v)
const arrayOf = (check: (v: unknown) => boolean) => (v: unknown) => Array.isArray(v) && v.every(check)

const isProject = (v: unknown): v is Project => isObj(v) && isInt(v.id) && isStr(v.name) && isStr(v.path) && isStr(v.created_at) && isBool(v.indexed)
  && isInt(v.files) && isInt(v.chunks) && isInt(v.flagged_files) && isStr(v.index_state)

const isIndexStatus = (v: unknown): v is IndexStatus => isObj(v) && isStr(v.state) && INDEX_STATES.includes(v.state) && isInt(v.project_id)
  && (v.files_done === undefined || isNum(v.files_done)) && (v.files_total === undefined || isNum(v.files_total))
  && (v.message === undefined || orNull(isStr)(v.message)) && (v.current_file === undefined || orNull(isStr)(v.current_file))
  && (v.skipped === undefined || arrayOf((s) => isObj(s) && isStr(s.path) && isStr(s.reason))(v.skipped))

const isSource = (v: unknown): v is Source => isObj(v) && isStr(v.path) && orNull(isStr)(v.symbol) && orNull(isStr)(v.parent) && isStr(v.kind) && isInt(v.start_line)
  && isInt(v.end_line) && isNum(v.score) && isBool(v.stale) && isBool(v.complete) && isBool(v.narrowed)

export const isAnswer = (v: unknown): v is Answer => isObj(v) && isStr(v.answer) && isBool(v.truncated) && orNull(isStr)(v.finish_reason) && orNull(isStr)(v.notice)
  && isBool(v.llm_called) && orNull(isStr)(v.model) && isStr(v.profile)
  && (v.usage === null || (isObj(v.usage) && orNull(isNum)(v.usage.prompt_tokens) && orNull(isNum)(v.usage.completion_tokens)))
  && isBool(v.sent_off_machine) && arrayOf(isSource)(v.sources) && arrayOf((d) => isObj(d) && isStr(d.path))(v.dropped)
  && arrayOf((h) => isObj(h) && isStr(h.path) && isStr(h.reason))(v.hidden_files) && arrayOf(isStr)(v.stale_files) && arrayOf(isStr)(v.deleted_files)
  && isNum(v.context_tokens_used) && isNum(v.context_budget) && isBool(v.over_budget) && orNull(isNum)(v.best_score) && isNum(v.k)
  && orNull(isStr)(v.ranking_note) && orNull(isStr)(v.calibration_note)

const isConversationSummary = (v: unknown): v is ConversationSummary => isObj(v) && isInt(v.id) && orNull(isStr)(v.title) && isStr(v.created_at) && isStr(v.updated_at)
  && isInt(v.message_count)

const isSavedMessage = (v: unknown): boolean => isObj(v) && isInt(v.id) && (v.role === 'user' || v.role === 'assistant') && isStr(v.content) && isStr(v.created_at)
  && (v.meta === null || isObj(v.meta))

const isConversation = (v: unknown): v is Conversation => isObj(v) && isInt(v.id) && orNull(isStr)(v.title) && isStr(v.created_at) && isStr(v.updated_at)
  && arrayOf(isSavedMessage)(v.messages)

const isLlmActive = (v: unknown): boolean => isObj(v) && isStr(v.preset) && isStr(v.label) && isStr(v.base_url) && isStr(v.model) && isInt(v.context_tokens)
  && isInt(v.max_output_tokens) && isBool(v.local) && isBool(v.takes_key) && isBool(v.key_optional) && isBool(v.key_set) && orNull(isStr)(v.key_source)

const isLlmPreset = (v: unknown): boolean => isObj(v) && isStr(v.id) && isStr(v.label) && isStr(v.base_url) && isStr(v.model) && isBool(v.takes_key) && isBool(v.key_optional)
  && isBool(v.local) && isBool(v.base_url_editable) && isInt(v.context_tokens) && isInt(v.max_output_tokens) && isStr(v.note)

const isLlmSettings = (v: unknown): v is LlmSettings => isObj(v) && isLlmActive(v.active) && arrayOf(isLlmPreset)(v.presets)

const isLlmTest = (v: unknown): v is LlmTest => isObj(v) && isBool(v.ok) && isStr(v.model) && isInt(v.latency_ms) && orNull(isStr)(v.finish_reason) && isStr(v.reply)

const isHealth = (v: unknown): v is Health => isObj(v) && isStr(v.status) && isStr(v.embedder) && orNull(isStr)(v.model) && orNull(isStr)(v.detail)

function fail<T>(code: string, message: string, status = 0): Result<T> {
  return { ok: false, error: { code, message, status } }
}

const BAD_REPLY = 'Clank got a reply it could not read. The app and its backend may be out of date.'
const validId = (id: unknown): id is number => typeof id === 'number' && Number.isInteger(id) && id > 0

export function createApi(bridge: Bridge | undefined) {
  async function call<T>(method: 'GET' | 'POST' | 'DELETE', path: string, body: object | undefined, valid: (v: unknown) => boolean): Promise<Result<T>> {
    if (!bridge) return fail('no_bridge', 'Clank can only reach its backend from the desktop app. Open it with the Clank desktop app, not in a browser.')
    let reply: unknown
    try {
      reply = await bridge.request(method, path, body)
    } catch {
      return fail('backend_unavailable', 'The Clank backend is not reachable.')           // the raw error is not passed on
    }
    return interpret<T>(reply, valid)
  }

  const withId = <T>(id: unknown, run: (id: number) => Promise<Result<T>>): Promise<Result<T>> =>
    validId(id) ? run(id) : Promise.resolve(fail<T>('bad_request', 'That project id is not valid.'))

  const withIds = <T>(id: unknown, conversationId: unknown, run: (id: number, conversationId: number) => Promise<Result<T>>): Promise<Result<T>> =>
    validId(id) && validId(conversationId) ? run(id, conversationId) : Promise.resolve(fail<T>('bad_request', 'That project or conversation id is not valid.'))

  return {
    health: () => call<Health>('GET', '/health', undefined, isHealth),
    listProjects: () => call<Project[]>('GET', '/projects', undefined, arrayOf(isProject)),
    addProject: (path: string, name?: string) => call<Project>('POST', '/projects', name === undefined ? { path } : { path, name }, isProject),
    deleteProject: (id: number) => withId(id, (i) => call<null>('DELETE', `/projects/${i}`, undefined, (v) => v === null || v === undefined)),
    startIndex: (id: number) => withId(id, (i) => call<IndexStatus>('POST', `/projects/${i}/index`, undefined, isIndexStatus)),
    indexStatus: (id: number) => withId(id, (i) => call<IndexStatus>('GET', `/projects/${i}/index`, undefined, isIndexStatus)),
    cancelIndex: (id: number) => withId(id, (i) => call<IndexStatus>('POST', `/projects/${i}/index/cancel`, undefined, isIndexStatus)),
    ask: (id: number, question: string, allowRemote: boolean, k?: number, conversationId?: number): Promise<Result<Answer>> => {
      if (typeof question !== 'string' || question.trim() === '') return Promise.resolve(fail('empty_question', 'Type a question first.'))
      if (conversationId !== undefined && !validId(conversationId)) return Promise.resolve(fail('bad_request', 'That conversation id is not valid.'))
      const body: Record<string, unknown> = { question, allow_remote: allowRemote }
      if (k !== undefined) body.k = k
      if (conversationId !== undefined) body.conversation_id = conversationId
      return withId(id, (i) => call<Answer>('POST', `/projects/${i}/answer`, body, isAnswer))
    },
    listConversations: (id: number) => withId(id, (i) => call<ConversationSummary[]>('GET', `/projects/${i}/conversations`, undefined, arrayOf(isConversationSummary))),
    createConversation: (id: number) => withId(id, (i) => call<ConversationSummary>('POST', `/projects/${i}/conversations`, undefined, isConversationSummary)),
    getConversation: (id: number, conversationId: number) => withIds(id, conversationId, (i, c) => call<Conversation>('GET', `/projects/${i}/conversations/${c}`, undefined, isConversation)),
    deleteConversation: (id: number, conversationId: number) => withIds(id, conversationId, (i, c) => call<null>('DELETE', `/projects/${i}/conversations/${c}`, undefined, (v) => v === null || v === undefined)),
    getLlmSettings: () => call<LlmSettings>('GET', '/settings/llm', undefined, isLlmSettings),
    testLlm: () => call<LlmTest>('POST', '/settings/llm/test', undefined, isLlmTest),
    async saveLlmSettings(config: object, apiKey?: string | null): Promise<Result<LlmSettings>> {
      if (!bridge) return fail('no_bridge', 'Saving settings only works in the Clank desktop app.')
      let reply: unknown
      try {
        reply = await bridge.saveLlmSettings(config, apiKey)
      } catch {
        return fail('backend_unavailable', 'The Clank backend is not reachable.')          // the raw error is not passed on: it could hold the key
      }
      return interpret<LlmSettings>(reply, isLlmSettings)
    },
    async pickFolder(): Promise<Result<string | null>> {
      if (!bridge) return fail('no_bridge', 'Choosing a folder only works in the Clank desktop app.')
      let reply: unknown
      try {
        reply = await bridge.pickFolder()
      } catch {
        return fail('backend_unavailable', 'The folder dialog could not be opened.')
      }
      const result = interpret<{ path: string | null }>(reply, (v) => isObj(v) && orNull(isStr)(v.path))
      return result.ok ? { ok: true, data: result.data.path } : result
    },
  }
}

function interpret<T>(reply: unknown, valid: (v: unknown) => boolean): Result<T> {
  if (!isObj(reply) || !isBool(reply.ok) || !isNum(reply.status)) return fail('bad_response', BAD_REPLY)
  if (!reply.ok) return errorFrom(reply.status, reply.body)
  if (!valid(reply.body)) return fail('bad_response', BAD_REPLY, reply.status)
  return { ok: true, data: (reply.body === undefined ? null : reply.body) as T }
}

function errorFrom<T>(status: number, body: unknown): Result<T> {
  const error = isObj(body) && isObj(body.error) ? body.error : null
  if (error && isStr(error.code) && isStr(error.message)) {
    const known: ApiError = { code: error.code, message: error.message, status }
    return { ok: false, error: known }
  }
  return fail('unknown_error', `Something went wrong (status ${status}).`, status)
}
