import { Gauge } from 'lucide-react'
import type { UsageReport } from '../api/types'
import { meterLabel, type MeterTone } from '../lib/usage'

const TONE: Record<MeterTone, string> = {
  quiet: 'bg-fill text-label-2 hover:bg-fill-strong',
  warn: 'bg-warn-soft text-warn hover:brightness-95',
  danger: 'bg-danger-soft text-danger hover:brightness-95',
}

/** The limit the person is closest to, beside the question bar: quiet while there is room, amber from 75%, red from 90%. A click opens the Usage tab. Nothing while no limit is known. */
function UsageMeter({ report, onOpen }: { report: UsageReport | null, onOpen: () => void }) {
  if (!report?.closest) return null
  const { text, tone, title } = meterLabel(report.closest)
  return (
    <button type="button" onClick={onOpen} title={title} aria-label={`Usage: ${title}`} className={`press flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium tabular-nums transition-colors duration-200 ${TONE[tone]}`}>
      <Gauge className="h-3 w-3 shrink-0" strokeWidth={1.75} />
      <span>{text}</span>
    </button>
  )
}

export default UsageMeter
