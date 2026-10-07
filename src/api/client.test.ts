// The window's typed wrapper around the backend bridge (`window.clankBackend`). What is promised:
// - every method turns the bridge's {ok, status, body} into a Result: the data, or an error {code, message, status} written for people;
// - a reply that does not have the shape the window relies on is "bad_response", never half-used (the backend and the window can drift apart);
// - a bridge that throws or is missing is "backend_unavailable"; nothing here ever throws;
// - ids are checked before anything is sent; a blank question is refused locally.
import test from 'node:test'
import assert from 'node:assert/strict'
import { createApi, type Bridge } from './client.ts'

type Call = { method: string, path: string, body?: object }

function bridgeReplying(reply: { ok: boolean, status: number, body: unknown } | ((c: Call) => unknown)) {
  const calls: Call[] = []
  const saves: Array<{ config: object, apiKey?: string | null }> = []
  const bridge: Bridge = {
    request: async (method, path, body) => {
      const call = { method, path, body }
      calls.push(call)
      return (typeof reply === 'function' ? reply(call) : reply) as never
    },
    pickFolder: async () => ({ ok: true, status: 200, body: { path: '/Users/x/proj' } }),
    saveLlmSettings: async (config: object, apiKey?: string | null) => { saves.push({ config, apiKey }); return (typeof reply === 'function' ? reply({ method: 'SAVE', path: 'save', body: config }) : reply) as never },
  }
  return { bridge, calls, saves }
}

const project = { id: 3, name: 'clank', path: '/Users/x/clank', created_at: '2026-10-06T10:00:00', indexed: true, files: 12, chunks: 80, flagged_files: 0, index_state: 'done' }
const source = { path: 'a.py', symbol: 'f', parent: null, kind: 'function', start_line: 1, end_line: 9, score: 0.7, stale: false, complete: true, narrowed: false }
const answer = {
  answer: 'It works.', truncated: false, finish_reason: 'stop', notice: null, llm_called: true, model: 'm', profile: 'groq', usage: { prompt_tokens: 10, completion_tokens: 5 },
  sent_off_machine: true, sources: [source], dropped: [], hidden_files: [], stale_files: [], deleted_files: [], context_tokens_used: 100, context_budget: 2500,
  over_budget: false, best_score: 0.7, k: 10, ranking_note: null, calibration_note: null,
}

test('listProjects returns the projects', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: [project] })
  const r = await createApi(bridge).listProjects()
  assert.deepEqual(r, { ok: true, data: [project] })
  assert.deepEqual(calls, [{ method: 'GET', path: '/projects', body: undefined }])
})

test('addProject sends the path (and the name only when given)', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 201, body: project })
  const api = createApi(bridge)
  await api.addProject('/Users/x/clank')
  await api.addProject('/Users/x/clank', 'My name')
  assert.deepEqual(calls.map((c) => c.body), [{ path: '/Users/x/clank' }, { path: '/Users/x/clank', name: 'My name' }])
  assert.equal(calls[0].method, 'POST')
})

test('deleteProject accepts the empty 204 answer', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 204, body: null })
  assert.deepEqual(await createApi(bridge).deleteProject(3), { ok: true, data: null })
  assert.deepEqual(calls, [{ method: 'DELETE', path: '/projects/3', body: undefined }])
})

test('the index calls use the project id in the path', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 202, body: { state: 'running', project_id: 3, files_done: 0, files_total: 0, current_file: null, message: null, skipped: [], error: null, stopped_kind: null } })
  const api = createApi(bridge)
  await api.startIndex(3)
  await api.indexStatus(3)
  await api.cancelIndex(3)
  assert.deepEqual(calls.map((c) => `${c.method} ${c.path}`), ['POST /projects/3/index', 'GET /projects/3/index', 'POST /projects/3/index/cancel'])
})

test('an idle index status has only a state and the project id', async () => {
  const { bridge } = bridgeReplying({ ok: true, status: 200, body: { state: 'idle', project_id: 3 } })
  const r = await createApi(bridge).indexStatus(3)
  assert.equal(r.ok && r.data.state, 'idle')
})

test('ask sends the question, the consent flag and k only when given', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: answer })
  const api = createApi(bridge)
  const r = await api.ask(3, 'how does it work?', true)
  await api.ask(3, 'q', false, 5)
  assert.deepEqual(r, { ok: true, data: answer })
  assert.deepEqual(calls.map((c) => c.body), [{ question: 'how does it work?', allow_remote: true }, { question: 'q', allow_remote: false, k: 5 }])
  assert.equal(calls[0].path, '/projects/3/answer')
})

test('a blank question is refused without calling the backend', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: answer })
  for (const q of ['', '   ', '\n\t']) {
    const r = await createApi(bridge).ask(3, q, true)
    assert.equal(r.ok, false)
    assert.equal(r.ok === false && r.error.code, 'empty_question')
  }
  assert.equal(calls.length, 0)
})

test('an id that is not a positive whole number is refused without calling the backend', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: {} })
  const api = createApi(bridge)
  for (const id of [0, -1, 1.5, NaN, Infinity, '3' as never, null as never]) {
    for (const r of [await api.deleteProject(id), await api.startIndex(id), await api.indexStatus(id), await api.cancelIndex(id), await api.ask(id, 'q', true)]) {
      assert.equal(r.ok, false)
      assert.equal(r.ok === false && r.error.code, 'bad_request')
    }
  }
  assert.equal(calls.length, 0)
})

test('the backend\'s own error is passed on in its own words', async () => {
  const body = { error: { code: 'consent_required', message: 'The answer model is a remote service. Allow it and ask again.' } }
  const { bridge } = bridgeReplying({ ok: false, status: 403, body })
  const r = await createApi(bridge).ask(3, 'q', false)
  assert.deepEqual(r, { ok: false, error: { code: 'consent_required', message: body.error.message, status: 403 } })
})

test('the bridge\'s own errors (status 0) keep their code', async () => {
  const body = { error: { code: 'backend_unavailable', message: 'The Clank backend is not running.' } }
  const { bridge } = bridgeReplying({ ok: false, status: 0, body })
  const r = await createApi(bridge).listProjects()
  assert.deepEqual(r, { ok: false, error: { code: 'backend_unavailable', message: body.error.message, status: 0 } })
})

test('an error with no readable body is a generic one that names the status', async () => {
  for (const body of [null, 'oops', 5, {}, { error: 'x' }, { error: { code: 5, message: 6 } }]) {
    const { bridge } = bridgeReplying({ ok: false, status: 502, body })
    const r = await createApi(bridge).listProjects()
    assert.equal(r.ok, false)
    assert.equal(r.ok === false && r.error.code, 'unknown_error')
    assert.match(r.ok === false ? r.error.message : '', /502/)
  }
})

test('a good status with the wrong shape is a bad response, not data', async () => {
  const cases: Array<[string, unknown]> = [
    ['listProjects', { not: 'a list' }], ['listProjects', [{ id: 'x' }]], ['listProjects', [{ ...project, files: '12' }]], ['listProjects', [{ ...project, index_state: 5 }]],
    ['addProject', null], ['addProject', { ...project, path: undefined }],
    ['indexStatus', { state: 'sleeping', project_id: 3 }], ['indexStatus', { project_id: 3 }], ['indexStatus', 'x'],
    ['ask', { ...answer, sources: 'none' }], ['ask', { ...answer, answer: 5 }], ['ask', { ...answer, usage: 5 }], ['ask', { ...answer, sources: [{ path: 'a.py' }] }], ['ask', null],
  ]
  for (const [method, body] of cases) {
    const { bridge } = bridgeReplying({ ok: true, status: 200, body })
    const api = createApi(bridge)
    const r = method === 'ask' ? await api.ask(3, 'q', true) : method === 'addProject' ? await api.addProject('/x') : method === 'indexStatus' ? await api.indexStatus(3) : await api.listProjects()
    assert.equal(r.ok, false, `${method} ${JSON.stringify(body)}`)
    assert.equal(r.ok === false && r.error.code, 'bad_response')
  }
})

test('an answer that was never sent to the model has no usage and still parses', async () => {
  const none = { ...answer, answer: 'No matching code was found in this project for that question.', llm_called: false, model: null, finish_reason: null, usage: null, sources: [], sent_off_machine: false, best_score: null }
  const { bridge } = bridgeReplying({ ok: true, status: 200, body: none })
  assert.deepEqual(await createApi(bridge).ask(3, 'q', true), { ok: true, data: none })
})

test('a bridge that throws or rejects is "backend_unavailable", and nothing escapes', async () => {
  const throwing: Bridge = { request: async () => { throw new Error('ipc closed: secret detail') }, pickFolder: async () => { throw new Error('x') }, saveLlmSettings: async () => { throw new Error('y') } }
  const r = await createApi(throwing).listProjects()
  assert.equal(r.ok, false)
  assert.equal(r.ok === false && r.error.code, 'backend_unavailable')
  assert.equal(JSON.stringify(r).includes('secret detail'), false, 'the raw error is not passed on')
})

test('no bridge at all (the page opened in a plain browser) is a clear error', async () => {
  const r = await createApi(undefined).listProjects()
  assert.equal(r.ok, false)
  assert.equal(r.ok === false && r.error.code, 'no_bridge')
  assert.match(r.ok === false ? r.error.message : '', /desktop app/i)
  assert.equal((await createApi(undefined).pickFolder()).ok, false)
})

test('pickFolder returns the chosen path, or null when the dialog was cancelled', async () => {
  const chosen = bridgeReplying({ ok: true, status: 200, body: null })
  assert.deepEqual(await createApi(chosen.bridge).pickFolder(), { ok: true, data: '/Users/x/proj' })
  const cancelled: Bridge = { request: async () => ({ ok: true, status: 200, body: null }), pickFolder: async () => ({ ok: true, status: 200, body: { path: null } }), saveLlmSettings: async () => ({ ok: true, status: 200, body: null }) }
  assert.deepEqual(await createApi(cancelled).pickFolder(), { ok: true, data: null })
  const odd: Bridge = { request: async () => ({ ok: true, status: 200, body: null }), pickFolder: async () => ({ ok: true, status: 200, body: { path: 5 } }), saveLlmSettings: async () => ({ ok: true, status: 200, body: null }) }
  const r = await createApi(odd).pickFolder()
  assert.equal(r.ok === false && r.error.code, 'bad_response')
})

test('health returns the report and never needs an id', async () => {
  const body = { status: 'ok', embedder: 'ready', model: 'q@1', detail: null }
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body })
  assert.deepEqual(await createApi(bridge).health(), { ok: true, data: body })
  assert.deepEqual(calls.map((c) => c.path), ['/health'])
})

// ---- the answer-model settings

const active = { preset: 'groq', label: 'Groq (hosted)', base_url: 'https://api.groq.com/openai/v1', model: 'openai/gpt-oss-120b', context_tokens: 2500, max_output_tokens: 1200,
  local: false, takes_key: true, key_optional: false, key_set: true, key_source: 'memory' }
const preset = { id: 'groq', label: 'Groq (hosted)', base_url: 'https://api.groq.com/openai/v1', model: 'openai/gpt-oss-120b', takes_key: true, key_optional: false, local: false,
  base_url_editable: false, context_tokens: 2500, max_output_tokens: 1200, note: 'A hosted service.' }
const llmSettings = { active, presets: [preset] }

test('getLlmSettings returns the active choice and the presets', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: llmSettings })
  assert.deepEqual(await createApi(bridge).getLlmSettings(), { ok: true, data: llmSettings })
  assert.deepEqual(calls, [{ method: 'GET', path: '/settings/llm', body: undefined }])
})

test('settings of the wrong shape are a bad response (the key must never be a field)', async () => {
  const bad: unknown[] = [null, {}, { active, presets: 'x' }, { active: { ...active, key_set: 'yes' }, presets: [preset] }, { active: { ...active, context_tokens: '2500' }, presets: [preset] },
    { active: { ...active, key_source: 5 }, presets: [preset] }, { active, presets: [{ ...preset, takes_key: 1 }] }, { active, presets: [{ id: 'x' }] }]
  for (const body of bad) {
    const { bridge } = bridgeReplying({ ok: true, status: 200, body })
    const r = await createApi(bridge).getLlmSettings()
    assert.equal(r.ok === false && r.error.code, 'bad_response', JSON.stringify(body))
  }
})

test('the connection test returns the model and the time it took', async () => {
  const body = { ok: true, model: 'served', latency_ms: 420, finish_reason: 'stop', reply: 'OK' }
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body })
  assert.deepEqual(await createApi(bridge).testLlm(), { ok: true, data: body })
  assert.deepEqual(calls, [{ method: 'POST', path: '/settings/llm/test', body: undefined }])
  const { bridge: bad } = bridgeReplying({ ok: true, status: 200, body: { ok: true, model: 5 } })
  assert.equal((await createApi(bad).testLlm()).ok, false)
  for (const broken of [{ model: 'm', latency_ms: 1, finish_reason: null, reply: 'x' }, { ok: 'yes', model: 'm', latency_ms: 1, finish_reason: null, reply: 'x' }, { ok: true, model: 'm', latency_ms: 1.5, finish_reason: null, reply: 'x' },
    { ok: true, model: 'm', latency_ms: 1, finish_reason: 5, reply: 'x' }, { ok: true, model: 'm', latency_ms: 1, finish_reason: null }]) {
    const { bridge: b2 } = bridgeReplying({ ok: true, status: 200, body: broken })
    assert.equal((await createApi(b2).testLlm()).ok, false, JSON.stringify(broken))
  }
})

test('saving settings goes through the desktop app\'s own door, with the key only when given', async () => {
  const { bridge, saves, calls } = bridgeReplying({ ok: true, status: 200, body: llmSettings })
  const api = createApi(bridge)
  assert.deepEqual(await api.saveLlmSettings({ preset: 'groq' }, 'a-key-123'), { ok: true, data: llmSettings })
  await api.saveLlmSettings({ preset: 'groq' })
  await api.saveLlmSettings({ preset: 'groq' }, null)
  assert.deepEqual(saves, [{ config: { preset: 'groq' }, apiKey: 'a-key-123' }, { config: { preset: 'groq' }, apiKey: undefined }, { config: { preset: 'groq' }, apiKey: null }])
  assert.equal(calls.length, 0, 'never through the generic request door: a key must not travel there')
})

test('a refused setting comes back as the backend\'s own error', async () => {
  const body = { error: { code: 'invalid_settings', message: 'The code budget must be a whole number from 500 to 16000.' } }
  const { bridge } = bridgeReplying({ ok: false, status: 422, body })
  assert.deepEqual(await createApi(bridge).saveLlmSettings({ preset: 'groq', context_tokens: 5 }), { ok: false, error: { code: 'invalid_settings', message: body.error.message, status: 422 } })
})

test('saving without a bridge, or with a bridge that throws, is an error and never an exception', async () => {
  assert.equal((await createApi(undefined).saveLlmSettings({ preset: 'groq' })).ok, false)
  assert.equal((await createApi(undefined).getLlmSettings()).ok, false)
  assert.equal((await createApi(undefined).testLlm()).ok, false)
  const throwing: Bridge = { request: async () => { throw new Error('x') }, pickFolder: async () => { throw new Error('x') }, saveLlmSettings: async () => { throw new Error('secret gsk_leak') } }
  const r = await createApi(throwing).saveLlmSettings({ preset: 'groq' }, 'gsk_leak')
  assert.equal(r.ok === false && r.error.code, 'backend_unavailable')
  assert.equal(JSON.stringify(r).includes('gsk_leak'), false)
})

// ---- saved conversations

const summary = { id: 7, title: 'how does it work?', created_at: '2026-10-07T10:00:00', updated_at: '2026-10-07T10:05:00', message_count: 2 }
const { answer: answerText, ...answerMeta } = answer
const saved = {
  id: 7, title: 'how does it work?', created_at: summary.created_at, updated_at: summary.updated_at,
  messages: [
    { id: 1, role: 'user', content: 'how does it work?', meta: null, created_at: summary.created_at },
    { id: 2, role: 'assistant', content: answerText, meta: answerMeta, created_at: summary.created_at },
  ],
}

test('the conversation calls use the project and conversation ids in the path', async () => {
  const { bridge, calls } = bridgeReplying((c) => ({ ok: true, status: c.method === 'DELETE' ? 204 : c.method === 'POST' ? 201 : 200, body: c.method === 'DELETE' ? null : c.path.endsWith('/7') ? saved : c.method === 'POST' ? summary : [summary] }))
  const api = createApi(bridge)
  assert.deepEqual(await api.listConversations(3), { ok: true, data: [summary] })
  assert.deepEqual(await api.createConversation(3), { ok: true, data: summary })
  assert.deepEqual(await api.getConversation(3, 7), { ok: true, data: saved })
  assert.deepEqual(await api.deleteConversation(3, 7), { ok: true, data: null })
  assert.deepEqual(calls.map((c) => `${c.method} ${c.path}`), ['GET /projects/3/conversations', 'POST /projects/3/conversations', 'GET /projects/3/conversations/7', 'DELETE /projects/3/conversations/7'])
})

test('ask sends the conversation id only when there is one', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: { ...answer, conversation_id: 7 } })
  const api = createApi(bridge)
  const r = await api.ask(3, 'q', true, undefined, 7)
  await api.ask(3, 'q', true)
  await api.ask(3, 'q', true, 5, 7)
  assert.deepEqual(calls.map((c) => c.body), [{ question: 'q', allow_remote: true, conversation_id: 7 }, { question: 'q', allow_remote: true }, { question: 'q', allow_remote: true, k: 5, conversation_id: 7 }])
  assert.equal(r.ok && r.data.conversation_id, 7)
})

test('a conversation id that is not a positive whole number is refused without calling the backend', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: {} })
  const api = createApi(bridge)
  for (const id of [0, -1, 1.5, NaN, '7' as never, null as never]) {
    for (const r of [await api.getConversation(3, id), await api.deleteConversation(3, id), await api.ask(3, 'q', true, undefined, id as never)]) {
      assert.equal(r.ok, false)
      assert.equal(r.ok === false && r.error.code, 'bad_request')
    }
  }
  assert.equal((await api.getConversation(0, 7)).ok, false)
  assert.equal(calls.length, 0)
})

test('a conversation reply with the wrong shape is bad_response', async () => {
  const wrong: Array<[string, unknown]> = [
    ['listConversations', [{ ...summary, id: 'x' }]], ['listConversations', [{ ...summary, message_count: null }]], ['listConversations', {}],
    ['createConversation', { ...summary, title: 5 }], ['getConversation', { ...saved, messages: 'none' }],
    ['getConversation', { ...saved, messages: [{ ...saved.messages[0], role: 'system' }] }], ['getConversation', { ...saved, messages: [{ ...saved.messages[0], meta: 'text' }] }],
    ['getConversation', { ...saved, messages: [{ ...saved.messages[0], content: 5 }] }],
  ]
  for (const [method, body] of wrong) {
    const { bridge } = bridgeReplying({ ok: true, status: 200, body })
    const api = createApi(bridge)
    const r = method === 'listConversations' ? await api.listConversations(3) : method === 'createConversation' ? await api.createConversation(3) : await api.getConversation(3, 7)
    assert.equal(r.ok === false && r.error.code, 'bad_response', `${method} ${JSON.stringify(body).slice(0, 60)}`)
  }
})

test('a conversation that is not there is an error written for people', async () => {
  const { bridge } = bridgeReplying({ ok: false, status: 404, body: { error: { code: 'conversation_not_found', message: 'There is no conversation with this id in this project.' } } })
  const r = await createApi(bridge).getConversation(3, 7)
  assert.deepEqual(r, { ok: false, error: { code: 'conversation_not_found', message: 'There is no conversation with this id in this project.', status: 404 } })
})

// ---- the source viewer

const view = { path: 'src/a.py', start_line: 3, end_line: 4, total_lines: 100, stale: false, lines: ['line 3', 'line 4'] }

test('getSource sends the path and the lines in the body, never in the address', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: view })
  const r = await createApi(bridge).getSource(3, 'src/a.py', 3, 4)
  assert.deepEqual(r, { ok: true, data: view })
  assert.deepEqual(calls, [{ method: 'POST', path: '/projects/3/source', body: { path: 'src/a.py', start_line: 3, end_line: 4 } }])
})

test('getSource refuses a bad id, path or range without calling the backend', async () => {
  const { bridge, calls } = bridgeReplying({ ok: true, status: 200, body: view })
  const api = createApi(bridge)
  const bad = [
    await api.getSource(0, 'a.py', 1, 2), await api.getSource(3, '', 1, 2), await api.getSource(3, 5 as never, 1, 2),
    await api.getSource(3, 'a.py', 0, 2), await api.getSource(3, 'a.py', 1.5, 2), await api.getSource(3, 'a.py', 5, 4), await api.getSource(3, 'a.py', '1' as never, 2),
  ]
  for (const r of bad) assert.equal(r.ok === false && r.error.code, 'bad_request')
  assert.equal(calls.length, 0)
})

test('a source reply with the wrong shape is bad_response', async () => {
  for (const body of [{ ...view, lines: 'x' }, { ...view, lines: [1] }, { ...view, stale: 'no' }, { ...view, total_lines: null }, { ...view, path: 5 }, null, []]) {
    const { bridge } = bridgeReplying({ ok: true, status: 200, body })
    const r = await createApi(bridge).getSource(3, 'a.py', 3, 4)
    assert.equal(r.ok === false && r.error.code, 'bad_response', JSON.stringify(body))
  }
})

test('a file that cannot be shown keeps the backend sentence', async () => {
  const body = { error: { code: 'file_not_available', message: 'This file cannot be shown. It may have moved, changed or never been indexed.' } }
  const { bridge } = bridgeReplying({ ok: false, status: 404, body })
  const r = await createApi(bridge).getSource(3, 'a.py', 1, 2)
  assert.deepEqual(r, { ok: false, error: { code: 'file_not_available', message: body.error.message, status: 404 } })
})
