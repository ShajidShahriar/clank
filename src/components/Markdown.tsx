import type { ReactNode } from 'react'
import { parseMarkdown, type Block, type Inline } from '../lib/markdown'

/** Draws a model's answer as real elements. Text is only ever drawn as text (React escapes it): there is no raw HTML and no clickable link. */
function inline(nodes: Inline[]): ReactNode {
  return nodes.map((node, i) => {
    switch (node.type) {
      case 'bold':
        return <strong key={i} className="font-semibold">{inline(node.children)}</strong>
      case 'italic':
        return <em key={i}>{inline(node.children)}</em>
      case 'code':
        return <code key={i} className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-white/10">{node.text}</code>
      case 'cite':
        return <span key={i} className="whitespace-nowrap rounded border border-gray-200 px-1 py-px font-mono text-[11px] text-gray-500 dark:border-white/15 dark:text-gray-400">{node.text}</span>
      default:
        return node.text
    }
  })
}

const HEADING_CLASS = ['', 'text-base font-semibold', 'text-[15px] font-semibold', 'text-sm font-semibold', 'text-sm font-medium', 'text-sm font-medium', 'text-sm font-medium']

function block(item: Block, i: number): ReactNode {
  switch (item.type) {
    case 'heading':
      return <p key={i} className={`${HEADING_CLASS[item.level]} [&:not(:first-child)]:mt-3`}>{inline(item.inline)}</p>
    case 'list': {
      const Tag = item.ordered ? 'ol' : 'ul'
      return (
        <Tag key={i} className={`${item.ordered ? 'list-decimal' : 'list-disc'} flex flex-col gap-1 pl-5 [&:not(:first-child)]:mt-2`}>
          {item.items.map((entry, j) => <li key={j} className="break-words">{inline(entry)}</li>)}
        </Tag>
      )
    }
    case 'table':
      return (
        <div key={i} className="overflow-x-auto [&:not(:first-child)]:mt-2">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>{item.header.map((cell, j) => <th key={j} className="border border-gray-200 bg-gray-50 px-2 py-1 text-left font-medium dark:border-white/10 dark:bg-white/5">{inline(cell)}</th>)}</tr>
            </thead>
            <tbody>
              {item.rows.map((row, r) => (
                <tr key={r}>{row.map((cell, j) => <td key={j} className="border border-gray-200 px-2 py-1 align-top dark:border-white/10">{inline(cell)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    case 'quote':
      return <blockquote key={i} className="whitespace-pre-wrap border-l-2 border-gray-300 pl-3 text-gray-600 dark:border-white/20 dark:text-gray-400 [&:not(:first-child)]:mt-2">{inline(item.inline)}</blockquote>
    case 'rule':
      return <hr key={i} className="my-3 border-gray-200 dark:border-white/10" />
    case 'code':
      return (
        <pre key={i} className="my-2 overflow-x-auto rounded-md border border-gray-200 bg-gray-50 p-2.5 font-mono text-xs leading-relaxed dark:border-white/10 dark:bg-black">
          <code>{item.text}</code>
        </pre>
      )
    default:
      return <p key={i} className="whitespace-pre-wrap break-words [&:not(:first-child)]:mt-2">{inline(item.inline)}</p>
  }
}

function Markdown({ text }: { text: string }) {
  const blocks = parseMarkdown(text)
  if (blocks.length === 0) return <p className="text-gray-500">The model sent an empty answer.</p>
  return <>{blocks.map(block)}</>
}

export default Markdown
