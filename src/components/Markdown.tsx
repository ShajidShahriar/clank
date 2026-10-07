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
        return <code key={i} className="rounded-[5px] bg-fill px-1 py-px font-mono text-[0.86em]">{node.text}</code>
      case 'cite':
        return <span key={i} className="whitespace-nowrap rounded-full bg-accent-soft px-1.5 py-px font-mono text-[11px] text-accent">{node.text}</span>
      default:
        return node.text
    }
  })
}

const HEADING_CLASS = ['', 'text-[17px] font-semibold tracking-[-0.015em]', 'text-[15px] font-semibold tracking-[-0.01em]', 'text-sm font-semibold', 'text-sm font-medium', 'text-sm font-medium', 'text-sm font-medium']

function block(item: Block, i: number): ReactNode {
  switch (item.type) {
    case 'heading':
      return <p key={i} className={`${HEADING_CLASS[item.level]} [&:not(:first-child)]:mt-5`}>{inline(item.inline)}</p>
    case 'list': {
      const Tag = item.ordered ? 'ol' : 'ul'
      return (
        <Tag key={i} className={`${item.ordered ? 'list-decimal' : 'list-disc'} flex flex-col gap-1 pl-5 [&:not(:first-child)]:mt-3`}>
          {item.items.map((entry, j) => <li key={j} className="break-words">{inline(entry)}</li>)}
        </Tag>
      )
    }
    case 'table':
      return (
        <div key={i} className="overflow-x-auto rounded-card border border-line [&:not(:first-child)]:mt-3">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>{item.header.map((cell, j) => <th key={j} className="border-b border-line bg-fill px-2.5 py-1.5 text-left font-medium">{inline(cell)}</th>)}</tr>
            </thead>
            <tbody>
              {item.rows.map((row, r) => (
                <tr key={r}>{row.map((cell, j) => <td key={j} className="border-b border-line px-2.5 py-1.5 align-top">{inline(cell)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )
    case 'quote':
      return <blockquote key={i} className="whitespace-pre-wrap border-l-[3px] border-fill-strong pl-3 text-label-2 [&:not(:first-child)]:mt-3">{inline(item.inline)}</blockquote>
    case 'rule':
      return <hr key={i} className="my-4 border-line" />
    case 'code':
      return (
        <pre key={i} className="my-3 overflow-x-auto rounded-card bg-card p-3.5 font-mono text-xs leading-relaxed">
          <code>{item.text}</code>
        </pre>
      )
    default:
      return <p key={i} className="whitespace-pre-wrap break-words [&:not(:first-child)]:mt-3">{inline(item.inline)}</p>
  }
}

function Markdown({ text }: { text: string }) {
  const blocks = parseMarkdown(text)
  if (blocks.length === 0) return <p className="text-label-3">The model sent an empty answer.</p>
  return <>{blocks.map(block)}</>
}

export default Markdown
