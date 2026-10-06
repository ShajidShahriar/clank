// A small markdown reader for model answers. It produces a plain structure that React draws as elements: there is NO raw HTML anywhere, so nothing a model
// writes can run, and links are only ever words (the address stays visible). Supported: bold, italic, inline code, the model's own citation brackets,
// headings, bulleted and numbered lists, tables, quotes, rules and fenced code blocks.
// Every scan is linear, so a hostile answer (100,000 stars) cannot hang the window: a marker with no closing left in the text is remembered and not searched
// for again. Erasable TypeScript only, so that `node --test` can run it.
import { splitAnswer } from './present.ts'

export type Inline =
  | { type: 'text' | 'code' | 'cite', text: string }
  | { type: 'bold' | 'italic', children: Inline[] }

export type Block =
  | { type: 'paragraph' | 'quote', inline: Inline[] }
  | { type: 'heading', level: number, inline: Inline[] }
  | { type: 'list', ordered: boolean, items: Inline[][] }
  | { type: 'table', header: Inline[][], rows: Inline[][][] }
  | { type: 'code', text: string, lang: string }
  | { type: 'rule' }

const isSpace = (ch: string | undefined) => ch === undefined || /\s/.test(ch)

export function parseInline(text: string): Inline[] {
  const out: Inline[] = []
  let buffer = ''
  let i = 0
  // An opener whose closing mark is a DIFFERENT character can repeat without ever closing (\u3010\u3010\u3010... or *a *a *a ...): once a scan has found no closing mark,
  // none exists later either, so it is remembered and never searched for again. (A backtick or a ** is its own closer, so one failed search ends them.)
  const dead = { italic: false, cite: false }
  const flush = () => {
    if (buffer) out.push({ type: 'text', text: buffer })
    buffer = ''
  }

  while (i < text.length) {
    const ch = text[i]

    if (ch === '`') {
      const close = text.indexOf('`', i + 1)
      if (close === i + 1) { buffer += '``'; i += 2; continue }
      else if (close !== -1) { flush(); out.push({ type: 'code', text: text.slice(i + 1, close) }); i = close + 1; continue }
    } else if (ch === '【' && !dead.cite) {
      const close = text.indexOf('】', i + 1)
      if (close === -1) dead.cite = true
      else if (close > i + 1) { flush(); out.push({ type: 'cite', text: text.slice(i + 1, close) }); i = close + 1; continue }
    } else if (ch === '*') {
      if (text[i + 1] === '*') {
        const close = text.indexOf('**', i + 2)
        if (close !== -1 && text.slice(i + 2, close).trim() !== '') { flush(); out.push({ type: 'bold', children: parseInline(text.slice(i + 2, close)) }); i = close + 2; continue }
        buffer += '**'
        i += 2
        continue
      }
      if (!dead.italic && !isSpace(text[i + 1]) && text[i + 1] !== undefined) {
        let close = text.indexOf('*', i + 1)
        while (close !== -1 && (isSpace(text[close - 1]) || text[close - 1] === '*' || text[close + 1] === '*')) close = text.indexOf('*', close + 1)
        if (close === -1) dead.italic = true                    // no valid closing star anywhere after this point: none for a later opener either
        else { flush(); out.push({ type: 'italic', children: parseInline(text.slice(i + 1, close)) }); i = close + 1; continue }
      }
    }
    buffer += ch
    i += 1
  }
  flush()
  return out
}

const HEADING = /^(#{1,6}) +(.*\S)\s*$/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/
const QUOTE = /^\s*>\s?(.*)$/
const BULLET = /^(\s*)[-*+]\s+(.*)$/
const NUMBERED = /^(\s*)\d+[.)]\s+(.*)$/
const TABLE_SEPARATOR = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/

const cells = (line: string): Inline[][] => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => parseInline(cell.trim()))

function startsTable(lines: string[], at: number): boolean {
  return lines[at].includes('|') && at + 1 < lines.length && lines[at + 1].includes('-') && TABLE_SEPARATOR.test(lines[at + 1])
}

function startsBlock(lines: string[], at: number): boolean {
  const line = lines[at]
  return HEADING.test(line) || RULE.test(line) || QUOTE.test(line) || BULLET.test(line) || NUMBERED.test(line) || startsTable(lines, at)
}

function parseBlocks(text: string): Block[] {
  const lines = text.split('\n')
  const blocks: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.trim() === '') { i += 1; continue }

    const heading = HEADING.exec(line)
    if (heading) { blocks.push({ type: 'heading', level: heading[1].length, inline: parseInline(heading[2]) }); i += 1; continue }
    if (RULE.test(line)) { blocks.push({ type: 'rule' }); i += 1; continue }

    if (QUOTE.test(line)) {
      const quoted: string[] = []
      while (i < lines.length && QUOTE.test(lines[i])) { quoted.push((QUOTE.exec(lines[i]) as RegExpExecArray)[1]); i += 1 }
      blocks.push({ type: 'quote', inline: parseInline(quoted.join('\n')) })
      continue
    }

    if (startsTable(lines, i)) {
      const header = cells(lines[i])
      i += 2
      const rows: Inline[][][] = []
      while (i < lines.length && lines[i].trim() !== '' && lines[i].includes('|')) { rows.push(cells(lines[i])); i += 1 }
      blocks.push({ type: 'table', header, rows })
      continue
    }

    const bullet = BULLET.test(line)
    if (bullet || NUMBERED.test(line)) {
      const kind = bullet ? BULLET : NUMBERED
      const items: string[] = []
      while (i < lines.length && lines[i].trim() !== '') {
        const item = kind.exec(lines[i])
        if (item && !RULE.test(lines[i])) items.push(item[2].trim())
        else if (/^\s+\S/.test(lines[i]) && items.length > 0 && !BULLET.test(lines[i]) && !NUMBERED.test(lines[i])) items[items.length - 1] += ` ${lines[i].trim()}`
        else break
        i += 1
      }
      blocks.push({ type: 'list', ordered: !bullet, items: items.map(parseInline) })
      continue
    }

    const paragraph: string[] = [line]
    i += 1
    while (i < lines.length && lines[i].trim() !== '' && !startsBlock(lines, i)) { paragraph.push(lines[i]); i += 1 }
    blocks.push({ type: 'paragraph', inline: parseInline(paragraph.join('\n')) })
  }
  return blocks
}

/** An answer as blocks: fenced code blocks first (nothing inside them is read as markdown), the rest as prose blocks. */
export function parseMarkdown(text: string): Block[] {
  const blocks: Block[] = []
  for (const part of splitAnswer(text)) {
    if (part.kind === 'code') blocks.push({ type: 'code', text: part.text, lang: part.lang })
    else blocks.push(...parseBlocks(part.text))
  }
  return blocks
}
