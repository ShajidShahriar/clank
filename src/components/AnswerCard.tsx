import { memo } from 'react'
import { Cloud } from 'lucide-react'
import type { Answer, Source } from '../api/types'
import { summaryLine } from '../lib/liveAnswer'
import { answerWarnings, tokenSummary } from '../lib/present'
import Markdown from './Markdown'
import SourcesList from './SourcesList'

/** The model's answer (prose and code blocks), the warnings that matter, its sources, and a small line of facts about how it was made. */
function AnswerCard({ answer, onOpenSource }: { answer: Answer, onOpenSource: (source: Source) => void }) {
  const warnings = answerWarnings(answer)
  const facts = [answer.model, summaryLine(answer) ?? tokenSummary(answer.usage)].filter(Boolean).join(' · ')

  return (
    <div className="animate-rise min-w-0">
      <div className="selectable min-w-0 text-sm leading-[1.6] text-label">
        <Markdown text={answer.answer} />
      </div>

      {warnings.length > 0 && (
        <ul className="mt-3 flex flex-col gap-0.5 rounded-control bg-warn-soft px-3 py-2 text-xs text-warn">
          {warnings.map((warning) => <li key={warning}>{warning}</li>)}
        </ul>
      )}

      <SourcesList sources={answer.sources} onOpen={onOpenSource} />

      {(facts || answer.sent_off_machine) && (
        <div className="mt-2.5 flex items-center gap-1.5 text-[11px] text-label-3">
          {answer.sent_off_machine && <Cloud className="h-3 w-3" strokeWidth={1.75} aria-label="Sent to a remote model" />}
          <span>{facts}</span>
        </div>
      )}
    </div>
  )
}

export default memo(AnswerCard)
