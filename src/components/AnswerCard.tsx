import { Bot, Cloud } from 'lucide-react'
import type { Answer } from '../api/types'
import { answerWarnings, tokenSummary } from '../lib/present'
import Markdown from './Markdown'
import SourcesList from './SourcesList'

/** The model's answer (prose and code blocks), the warnings that matter, its sources, and a small line of facts about how it was made. */
function AnswerCard({ answer }: { answer: Answer }) {
  const warnings = answerWarnings(answer)
  const facts = [answer.model, tokenSummary(answer.usage)].filter(Boolean).join(' · ')

  return (
    <div className="flex items-start gap-2">
      <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-100 dark:bg-gray-800">
        <Bot className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
      </div>
      <div className="min-w-0 max-w-[85%] rounded-lg border border-gray-200 bg-white px-3.5 py-2.5 text-sm leading-relaxed text-gray-900 dark:border-white/10 dark:bg-[#0a0a0a] dark:text-gray-100">
        <Markdown text={answer.answer} />

        {warnings.length > 0 && (
          <ul className="mt-2 flex flex-col gap-0.5 text-xs text-amber-700 dark:text-amber-300">
            {warnings.map((warning) => <li key={warning}>{warning}</li>)}
          </ul>
        )}

        <SourcesList sources={answer.sources} />

        {(facts || answer.sent_off_machine) && (
          <div className="mt-2 flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-500">
            {answer.sent_off_machine && <Cloud className="h-3 w-3" aria-label="Sent to a remote model" />}
            <span>{facts}</span>
          </div>
        )}
      </div>
    </div>
  )
}

export default AnswerCard
