// Streaming answers, step 1.3, part 1: reading the backend's NDJSON stream safely (pure: no network, no timers).
// What is promised:
// - bytes are cut into lines at "\n" (and "\r\n"); a line or a character cut across two chunks is put back together; blank lines say nothing;
// - memory is bounded: a line that grows past the limit WITHOUT an end, or a stream past the total limit, is refused at once;
// - bytes that are not valid text are refused (never silently replaced);
// - a line is an event only if it is a JSON OBJECT whose `type` is one of the six the backend sends; anything else is refused;
// - many small events are batched: neighbouring pieces of text become one, neighbouring thinking counts keep the latest, and nothing else changes or moves.
import test from 'node:test'
import assert from 'node:assert/strict'
import { LineReader, StreamFormatError, StreamLimitError, coalesce, isTerminal, parseEvent } from './stream-events.ts'

const enc = (text: string) => new TextEncoder().encode(text)

// ---- cutting bytes into lines

test('lines are cut at newlines and come back in order', () => {
  const r = new LineReader()
  assert.deepEqual(r.push(enc('{"a":1}\n{"b":2}\n')), ['{"a":1}', '{"b":2}'])
})

test('a line cut across two chunks is put back together', () => {
  const r = new LineReader()
  assert.deepEqual(r.push(enc('{"type":"del')), [])
  assert.deepEqual(r.push(enc('ta"}\n{"x"')), ['{"type":"delta"}'])
  assert.deepEqual(r.push(enc(':1}\n')), ['{"x":1}'])
})

test('a carriage return before the newline is not part of the line', () => {
  assert.deepEqual(new LineReader().push(enc('{"a":1}\r\n{"b":2}\r\n')), ['{"a":1}', '{"b":2}'])
})

test('blank lines say nothing', () => {
  assert.deepEqual(new LineReader().push(enc('\n\n{"a":1}\n   \n\r\n')), ['{"a":1}'])
})

test('a character cut across chunks (two, three and four bytes long) is decoded whole', () => {
  for (const text of ['café', '日本語', '🙂 end']) {
    const bytes = enc(`{"t":"${text}"}\n`)
    for (let cut = 1; cut < bytes.length; cut++) {
      const r = new LineReader()
      const lines = [...r.push(bytes.slice(0, cut)), ...r.push(bytes.slice(cut))]
      assert.deepEqual(lines, [`{"t":"${text}"}`], `${text} cut at ${cut}`)
    }
  }
})

test('one byte at a time works', () => {
  const r = new LineReader()
  const lines: string[] = []
  for (const byte of enc('{"t":"é🙂"}\n{"u":2}\n')) lines.push(...r.push(new Uint8Array([byte])))
  assert.deepEqual(lines, ['{"t":"é🙂"}', '{"u":2}'])
})

test('a last line without a newline comes out when the stream ends', () => {
  const r = new LineReader()
  assert.deepEqual(r.push(enc('{"a":1}\n{"b":2}')), ['{"a":1}'])
  assert.deepEqual(r.finish(), ['{"b":2}'])
  assert.deepEqual(r.finish(), [], 'only once')
})

test('nothing left means nothing to finish', () => {
  const r = new LineReader()
  r.push(enc('{"a":1}\n'))
  assert.deepEqual(r.finish(), [])
})

// ---- the limits

test('a line past the limit is refused even before its end arrives', () => {
  const r = new LineReader({ maxLineBytes: 50 })
  assert.deepEqual(r.push(enc('x'.repeat(40))), [])
  assert.throws(() => r.push(enc('y'.repeat(20))), (e: unknown) => e instanceof StreamLimitError && e.code === 'line_too_long')
})

test('a line just under the limit is fine, and the limit is per line, not for all of them', () => {
  const r = new LineReader({ maxLineBytes: 50 })
  const line = 'x'.repeat(49)
  assert.deepEqual(r.push(enc(`${line}\n${line}\n${line}\n`)), [line, line, line])
})

test('a complete line past the limit that arrives in one chunk is refused too', () => {
  const r = new LineReader({ maxLineBytes: 50 })
  assert.throws(() => r.push(enc('x'.repeat(60) + '\n')), (e: unknown) => e instanceof StreamLimitError && e.code === 'line_too_long')
})

test('a stream past the total limit is refused', () => {
  const r = new LineReader({ maxTotalBytes: 100 })
  r.push(enc('x'.repeat(30) + '\n'))
  r.push(enc('x'.repeat(30) + '\n'))
  assert.throws(() => r.push(enc('x'.repeat(60) + '\n')), (e: unknown) => e instanceof StreamLimitError && e.code === 'too_big')
})

test('the default limits are generous for answers and small for a flood', () => {
  const r = new LineReader()
  assert.deepEqual(r.push(enc('x'.repeat(500_000) + '\n')).length, 1)
  assert.throws(() => new LineReader().push(enc('x'.repeat(2_000_000))), StreamLimitError)
})

test('bytes that are not valid text are refused, not replaced', () => {
  assert.throws(() => new LineReader().push(new Uint8Array([0x7b, 0xff, 0xfe, 0x7d, 0x0a])), (e: unknown) => e instanceof StreamFormatError)
})

// ---- one line is one event

for (const type of ['start', 'stage', 'thinking', 'delta', 'done', 'error']) {
  test(`a ${type} event is accepted and keeps all its fields`, () => {
    assert.deepEqual(parseEvent(`{"type":"${type}","a":1,"nested":{"b":[1,2]}}`), { type, a: 1, nested: { b: [1, 2] } })
  })
}

for (const bad of ['', 'not json', '{"type":"delta"', '[]', '"delta"', '5', 'null', 'true', '{}', '{"type":5}', '{"type":null}', '{"type":"cancelled"}', '{"type":"other"}', '{"type":"DELTA"}',
  '{"type":["delta"]}', '{"kind":"delta"}']) {
  test(`${JSON.stringify(bad)} is not an event`, () => {
    assert.throws(() => parseEvent(bad), (e: unknown) => e instanceof StreamFormatError)
  })
}

test('the backend cannot send the app\'s own "cancelled" event', () => {
  assert.throws(() => parseEvent('{"type":"cancelled"}'), StreamFormatError)
})

test('only done and error end a stream', () => {
  assert.equal(isTerminal({ type: 'done' }), true)
  assert.equal(isTerminal({ type: 'error' }), true)
  assert.equal(isTerminal({ type: 'cancelled' }), true)
  for (const type of ['start', 'stage', 'thinking', 'delta']) assert.equal(isTerminal({ type }), false, type)
})

// ---- batching

const delta = (text: string) => ({ type: 'delta', text })
const thinking = (pieces: number) => ({ type: 'thinking', pieces })

test('neighbouring text pieces become one', () => {
  assert.deepEqual(coalesce([delta('a'), delta('b'), delta('c')]), [delta('abc')])
})

test('text pieces apart are not joined across another event', () => {
  const stage = { type: 'stage', stage: 'writing', at_ms: 5 }
  assert.deepEqual(coalesce([delta('a'), stage, delta('b')]), [delta('a'), stage, delta('b')])
})

test('neighbouring thinking counts keep only the latest', () => {
  assert.deepEqual(coalesce([thinking(1), thinking(2), thinking(3)]), [thinking(3)])
})

test('thinking counts apart keep their place', () => {
  const stage = { type: 'stage', stage: 'writing', at_ms: 5 }
  assert.deepEqual(coalesce([thinking(1), stage, thinking(2)]), [thinking(1), stage, thinking(2)])
})

test('the order of everything else is kept, and the last event stays last', () => {
  const events = [{ type: 'start', x: 1 }, { type: 'stage', stage: 'thinking', at_ms: 1 }, thinking(1), thinking(2), { type: 'stage', stage: 'writing', at_ms: 2 }, delta('a'), delta('b'), { type: 'done', answer: 'ab' }]
  assert.deepEqual(coalesce(events), [{ type: 'start', x: 1 }, { type: 'stage', stage: 'thinking', at_ms: 1 }, thinking(2), { type: 'stage', stage: 'writing', at_ms: 2 }, delta('ab'), { type: 'done', answer: 'ab' }])
})

test('the text is exactly the same after batching, whatever the pieces', () => {
  const pieces = ['  spaced ', '\n\n', 'é', '🙂', ' end ']
  const merged = coalesce(pieces.map(delta))
  assert.equal(merged.length, 1)
  assert.equal(merged[0].text, pieces.join(''))
})

test('batching does not change its input', () => {
  const events = [delta('a'), delta('b'), thinking(1), thinking(2)]
  const copy = JSON.parse(JSON.stringify(events))
  coalesce(events)
  assert.deepEqual(events, copy)
})

test('an empty batch stays empty and one event stays itself', () => {
  assert.deepEqual(coalesce([]), [])
  assert.deepEqual(coalesce([delta('a')]), [delta('a')])
})

test('a merged text event has only a type and the text', () => {
  assert.deepEqual(Object.keys(coalesce([{ type: 'delta', text: 'a', extra: 1 }, delta('b')])[0]).sort(), ['text', 'type'])
})
