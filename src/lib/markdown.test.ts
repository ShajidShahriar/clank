// The small markdown reader for model answers: bold, italic, inline code, headings, lists, tables, quotes, rules, code blocks and the model's own
// citation brackets. It produces a plain structure that React draws as elements: there is NO raw HTML anywhere, so nothing a model writes can run.
// It must also survive hostile input (a huge pile of markers) without hanging: every scan is linear.
import test from 'node:test'
import assert from 'node:assert/strict'
import { parseInline, parseMarkdown, type Inline } from './markdown.ts'

const t = (text: string): Inline => ({ type: 'text', text })
const b = (...children: Inline[]): Inline => ({ type: 'bold', children })
const i = (...children: Inline[]): Inline => ({ type: 'italic', children })
const c = (text: string): Inline => ({ type: 'code', text })
const cite = (text: string): Inline => ({ type: 'cite', text })

// ---- inline

test('plain text stays plain', () => {
  assert.deepEqual(parseInline('just words, 2 * 3 = 6.'), [t('just words, 2 * 3 = 6.')])
  assert.deepEqual(parseInline(''), [])
})

test('bold, italic and inline code', () => {
  assert.deepEqual(parseInline('a **Cursus** b'), [t('a '), b(t('Cursus')), t(' b')])
  assert.deepEqual(parseInline('an *odd* word'), [t('an '), i(t('odd')), t(' word')])
  assert.deepEqual(parseInline('call `f()` now'), [t('call '), c('f()'), t(' now')])
})

test('code inside bold, and bold never swallows code markers', () => {
  assert.deepEqual(parseInline('**the `x` value**'), [b(t('the '), c('x'), t(' value'))])
  assert.deepEqual(parseInline('`**not bold**`'), [c('**not bold**')])
})

test('the model\'s citation brackets become citations', () => {
  assert.deepEqual(parseInline('built with Strapi 【README.md:1-7】 .'), [t('built with Strapi '), cite('README.md:1-7'), t(' .')])
})

test('markers that are never closed stay as the words they were', () => {
  assert.deepEqual(parseInline('a **b'), [t('a **b')])
  assert.deepEqual(parseInline('a `b'), [t('a `b')])
  assert.deepEqual(parseInline('a 【b'), [t('a 【b')])
  assert.deepEqual(parseInline('a *b'), [t('a *b')])
  assert.deepEqual(parseInline('**'), [t('**')])
  assert.deepEqual(parseInline('****'), [t('****')])
  assert.deepEqual(parseInline('``'), [t('``')])
})

test('stars in globs and arithmetic are not italics', () => {
  assert.deepEqual(parseInline('files like *.py and *.ts'), [t('files like *.py and *.ts')])
  assert.deepEqual(parseInline('2 * 3 * 4'), [t('2 * 3 * 4')])
  assert.deepEqual(parseInline('a * b'), [t('a * b')])
})

test('a star before a space does not open an italic, even if a closing star follows', () => {
  assert.deepEqual(parseInline('a * b* c'), [t('a * b* c')])
})

test('an italic skips stars that belong to a bold marker and closes on the real one', () => {
  assert.deepEqual(parseInline('*a**b**c*'), [i(t('a'), b(t('b')), t('c'))])
  assert.deepEqual(parseInline('*a **b** c*'), [i(t('a '), b(t('b')), t(' c'))])
})

test('an empty citation is just the brackets', () => {
  assert.deepEqual(parseInline('a \u3010\u3011 b'), [t('a \u3010\u3011 b')])
})

test('underscores in names are left alone', () => {
  assert.deepEqual(parseInline('the snake_case_name and __dunder__'), [t('the snake_case_name and __dunder__')])
})

test('several pieces in one line', () => {
  assert.deepEqual(parseInline('a **b** c `d` e *f* g'), [t('a '), b(t('b')), t(' c '), c('d'), t(' e '), i(t('f')), t(' g')])
})

test('the characters of a line are never lost or invented', () => {
  const flat = (parts: Inline[]): string => parts.map((p) => ('children' in p ? flat(p.children) : p.text)).join('')
  const text = 'He said **hello** to `x` and *waved* 【a.py:1】 then left'
  assert.equal(flat(parseInline(text)), 'He said hello to x and waved a.py:1 then left')
})

// ---- blocks

test('paragraphs are separated by blank lines and keep their own line breaks', () => {
  assert.deepEqual(parseMarkdown('One.\nStill one.\n\nTwo.'), [
    { type: 'paragraph', inline: [t('One.\nStill one.')] },
    { type: 'paragraph', inline: [t('Two.')] },
  ])
  assert.deepEqual(parseMarkdown(''), [])
  assert.deepEqual(parseMarkdown('  \n\n '), [])
})

test('headings, with their level; a hash without a space is just text', () => {
  assert.deepEqual(parseMarkdown('# Title\n## Sub **bold**\n###### Deep'), [
    { type: 'heading', level: 1, inline: [t('Title')] },
    { type: 'heading', level: 2, inline: [t('Sub '), b(t('bold'))] },
    { type: 'heading', level: 6, inline: [t('Deep')] },
  ])
  assert.deepEqual(parseMarkdown('#notaheading'), [{ type: 'paragraph', inline: [t('#notaheading')] }])
  assert.deepEqual(parseMarkdown('####### seven'), [{ type: 'paragraph', inline: [t('####### seven')] }])
})

test('bulleted lists with -, * and +', () => {
  for (const mark of ['-', '*', '+']) {
    assert.deepEqual(parseMarkdown(`${mark} one\n${mark} two **b**`), [{ type: 'list', ordered: false, items: [[t('one')], [t('two '), b(t('b'))]] }], mark)
  }
})

test('numbered lists with 1. and 1)', () => {
  assert.deepEqual(parseMarkdown('1. first\n2. second'), [{ type: 'list', ordered: true, items: [[t('first')], [t('second')]] }])
  assert.deepEqual(parseMarkdown('1) first\n2) second'), [{ type: 'list', ordered: true, items: [[t('first')], [t('second')]] }])
})

test('an indented line continues the item before it, and a blank line ends the list', () => {
  assert.deepEqual(parseMarkdown('- one\n  more of one\n- two\n\nAfter.'), [
    { type: 'list', ordered: false, items: [[t('one more of one')], [t('two')]] },
    { type: 'paragraph', inline: [t('After.')] },
  ])
})

test('a bulleted list and a numbered list next to each other are two lists', () => {
  assert.deepEqual(parseMarkdown('- a\n1. b'), [
    { type: 'list', ordered: false, items: [[t('a')]] },
    { type: 'list', ordered: true, items: [[t('b')]] },
  ])
})

test('nested lists are flattened, never lost', () => {
  const blocks = parseMarkdown('- top\n  - child\n- next')
  const words = JSON.stringify(blocks)
  for (const word of ['top', 'child', 'next']) assert.ok(words.includes(word), word)
})

test('a table has a header, rows and cells that read inline markdown', () => {
  assert.deepEqual(parseMarkdown('| Name | Role |\n|------|:----:|\n| `a` | **admin** |\n| b | user |'), [{
    type: 'table',
    header: [[t('Name')], [t('Role')]],
    rows: [[[c('a')], [b(t('admin'))]], [[t('b')], [t('user')]]],
  }])
})

test('a table ends at the first line without a pipe, even with no blank line after it', () => {
  assert.deepEqual(parseMarkdown('| a | b |\n|---|---|\n| 1 | 2 |\nAfter the table.'), [
    { type: 'table', header: [[t('a')], [t('b')]], rows: [[[t('1')], [t('2')]]] },
    { type: 'paragraph', inline: [t('After the table.')] },
  ])
})

test('a rule inside a list ends the list', () => {
  assert.deepEqual(parseMarkdown('- a\n- - -\n- b'), [
    { type: 'list', ordered: false, items: [[t('a')]] },
    { type: 'rule' },
    { type: 'list', ordered: false, items: [[t('b')]] },
  ])
})

test('a list, heading, quote or rule right after a paragraph line starts its own block', () => {
  assert.deepEqual(parseMarkdown('Intro line\n- item'), [{ type: 'paragraph', inline: [t('Intro line')] }, { type: 'list', ordered: false, items: [[t('item')]] }])
  assert.deepEqual(parseMarkdown('Intro\n# Title'), [{ type: 'paragraph', inline: [t('Intro')] }, { type: 'heading', level: 1, inline: [t('Title')] }])
  assert.deepEqual(parseMarkdown('Intro\n> said'), [{ type: 'paragraph', inline: [t('Intro')] }, { type: 'quote', inline: [t('said')] }])
  assert.deepEqual(parseMarkdown('Intro\n---'), [{ type: 'paragraph', inline: [t('Intro')] }, { type: 'rule' }])
})

test('a line with pipes but no separator row is a paragraph', () => {
  assert.deepEqual(parseMarkdown('a | b | c'), [{ type: 'paragraph', inline: [t('a | b | c')] }])
})

test('quotes and rules', () => {
  assert.deepEqual(parseMarkdown('> quoted **text**'), [{ type: 'quote', inline: [t('quoted '), b(t('text'))] }])
  for (const rule of ['---', '***', '___', '- - -']) assert.deepEqual(parseMarkdown(rule), [{ type: 'rule' }], rule)
})

test('code blocks keep their text, indentation and language, and nothing inside them is read as markdown', () => {
  assert.deepEqual(parseMarkdown('Look:\n```python\n  # not a heading\n  x = **y**\n```\nDone.'), [
    { type: 'paragraph', inline: [t('Look:')] },
    { type: 'code', text: '  # not a heading\n  x = **y**', lang: 'python' },
    { type: 'paragraph', inline: [t('Done.')] },
  ])
})

test('an unclosed code block is still code', () => {
  assert.deepEqual(parseMarkdown('Here:\n```js\nlet a = 1'), [{ type: 'paragraph', inline: [t('Here:')] }, { type: 'code', text: 'let a = 1', lang: 'js' }])
})

test('a real answer of the kind a model writes', () => {
  const answer = 'The project is **Cursus**, a learning system built with **Strapi** 【README.md:1-7】.\n\n**Key pieces**\n\n- `proxy.ts` hides the token\n- roles: admin, student\n\n| File | Job |\n|---|---|\n| a.ts | auth |\n'
  const blocks = parseMarkdown(answer)
  assert.deepEqual(blocks.map((x) => x.type), ['paragraph', 'paragraph', 'list', 'table'])
  assert.deepEqual((blocks[0] as { inline: Inline[] }).inline.map((x) => x.type), ['text', 'bold', 'text', 'bold', 'text', 'cite', 'text'])
})

test('html in an answer is only ever text', () => {
  const blocks = parseMarkdown('<script>alert(1)</script> and <img src=x onerror=alert(1)>\n\n[click](javascript:alert(1))')
  assert.equal(JSON.stringify(blocks).includes('"type":"html"'), false)
  const everyType = new Set<string>()
  const walk = (node: unknown) => { if (Array.isArray(node)) node.forEach(walk); else if (node && typeof node === 'object') { everyType.add((node as { type?: string }).type ?? ''); Object.values(node).forEach(walk) } }
  walk(blocks)
  for (const kind of everyType) assert.ok(['', 'paragraph', 'text'].includes(kind), `unexpected node type ${kind}`)
})

test('a link is shown as its words, with the address visible and never as something to click', () => {
  const blocks = parseMarkdown('See [the docs](https://example.com/a) now')
  assert.equal(JSON.stringify(blocks).includes('https://example.com/a'), true)
  assert.equal(JSON.stringify(blocks).includes('"type":"link"'), false)
})

// ---- hostile input must not hang

for (const [name, text] of [
  ['100,000 stars', '*'.repeat(100000)],
  ['unclosed bold pairs', '**a '.repeat(30000)],
  ['100,000 backticks', '`'.repeat(100000)],
  ['400,000 citation openers with no closing bracket', '【'.repeat(400000)],
  ['100,000 stars each followed by a letter and a space', '*a '.repeat(100000)],
  ['alternating markers', '*a`b**c【'.repeat(20000)],
  ['a very long line of words', 'word '.repeat(100000)],
  ['20,000 list lines', '- item\n'.repeat(20000)],
  ['20,000 table rows', '|a|b|\n|-|-|\n' + '|1|2|\n'.repeat(20000)],
  ['20,000 headings', '# h\n'.repeat(20000)],
] as Array<[string, string]>) {
  test(`hostile input does not hang: ${name}`, () => {
    const started = Date.now()
    parseMarkdown(text)
    assert.ok(Date.now() - started < 2000, `took ${Date.now() - started} ms`)
  })
}

// ---- an answer being written (step 1.4): every half-written state must be readable

const SAMPLE = [
  '# Overview', '', 'The function **handle** in `src/app.py:10-20` calls *route*【src/app.py:10-20】 and then:', '',
  '1. parse the request', '2. look up the **handler**', '   - nested point', '',
  '| name | role |', '| --- | --- |', '| `a` | first |', '| b | **second** |', '', '> a quoted note', '', '---', '',
  '```python', 'def f(x):', '    return x * 2  # **not bold** `not code`', '```', '', 'Done. 日本語 🙂 and a lone *star and `tick',
].join('\n')

test('every half-written state of an answer can be read without an error', () => {
  for (let end = 0; end <= SAMPLE.length; end++) {
    assert.doesNotThrow(() => parseMarkdown(SAMPLE.slice(0, end)), `cut at ${end}`)
  }
})

test('a code block that has been opened and not closed yet is code, and its text is not read as markdown', () => {
  const blocks = parseMarkdown('Look:\n\n```python\ndef f():\n    return **x**')
  const code = blocks.find((block) => block.type === 'code')
  assert.ok(code && code.type === 'code')
  assert.equal(code.text, 'def f():\n    return **x**')
  assert.equal(code.lang, 'python')
})

test('a half-written fence (two backticks, or the opening line cut) never swallows the text before it', () => {
  for (const cut of ['text\n\n``', 'text\n\n```', 'text\n\n```py']) {
    const blocks = parseMarkdown(cut)
    assert.ok(blocks.length >= 1 && blocks[0].type === 'paragraph', JSON.stringify(cut))
  }
})

test('a half-written table row is still a table or plain text, never a crash', () => {
  for (const cut of ['| a | b |', '| a | b |\n| ---', '| a | b |\n| --- | --- |\n| 1 |', '| a | b |\n| --- | --- |\n| 1 | 2']) {
    assert.doesNotThrow(() => parseMarkdown(cut), JSON.stringify(cut))
  }
})

test('the text of a half-written answer is never lost: every visible character of a plain prefix is still there', () => {
  const plain = 'First paragraph with words.\n\nSecond paragraph, longer, with more words.'
  for (let end = 1; end <= plain.length; end++) {
    const blocks = parseMarkdown(plain.slice(0, end))
    const shown = blocks.flatMap((block) => (block.type === 'paragraph' ? block.inline : [])).map((piece) => (piece.type === 'text' ? piece.text : '')).join('')
    assert.equal(shown.replace(/\s+/g, ''), plain.slice(0, end).replace(/\s+/g, ''), `cut at ${end}`)
  }
})

test('growing the text never makes the earlier blocks change shape (a finished paragraph stays the same while the next one is written)', () => {
  const before = parseMarkdown('First paragraph.\n\nSecond')
  const after = parseMarkdown('First paragraph.\n\nSecond paragraph, now longer.')
  assert.deepEqual(before[0], after[0])
})
