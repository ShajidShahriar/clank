import { useMemo, useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { ApiError, UsageLimits, UsageReport } from '../api/types'
import { countsLines, limitText, meterTone, providerRowText, type MeterTone } from '../lib/usage'
import { WINDOW_ORDER, formFromReport, isUsageFormDirty, payloadFromUsageForm, validateUsageForm, type UsageForm } from '../lib/usageForm'
import { describeError } from '../lib/present'
import { BUTTON, BUTTON_PRIMARY, INPUT } from './ui'

interface UsageTabProps {
  report: UsageReport | null
  problem: ApiError | null
  onSaveLimits: (limits: UsageLimits | null) => Promise<ApiError | null>
  onReset: () => Promise<ApiError | null>
}

const FILL: Record<MeterTone, string> = { quiet: 'bg-accent', warn: 'bg-warn', danger: 'bg-danger' }

function Bar({ percent, reached }: { percent: number, reached: boolean }) {
  return (
    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-fill" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.floor(percent))}>
      <div className={`h-full rounded-full transition-[width] duration-500 ease-out ${FILL[meterTone({ percent, reached })]}`} style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
  )
}

function Section({ title, action, children }: { title: string, action?: React.ReactNode, children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-medium text-label-3">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

const cap = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

/** The Usage tab: your limits (and Groq's published ones until you set your own), what the provider itself reports, and what Clank has counted. Limits only warn. */
function UsageTab({ report, problem, onSaveLimits, onReset }: UsageTabProps) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<UsageForm | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const errors = useMemo(() => (form ? validateUsageForm(form) : {}), [form])

  if (!report) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center px-5 text-xs text-label-2">
        {problem ? <p className="selectable text-danger">{describeError(problem).message}</p> : <p className="flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" />Loading the usage…</p>}
      </div>
    )
  }

  const run = async (action: () => Promise<ApiError | null>, done: () => void) => {
    setBusy(true)
    setFailure(null)
    const error = await action()
    setBusy(false)
    if (error) setFailure(error)
    else done()
  }
  const startEditing = () => { setForm(formFromReport(report)); setFailure(null); setEditing(true) }
  const dirty = form !== null && isUsageFormDirty(form, report)
  const change = (window: keyof UsageForm, kind: 'tokens' | 'requests', value: string) => setForm((current) => (current ? { ...current, [window]: { ...current[window], [kind]: value } } : current))

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto px-5 py-5">
      <p className="text-xs text-label-3">{cap(report.provider)}{report.model ? ` · ${report.model}` : ''}</p>

      <Section title="Your limits" action={!editing && <button onClick={startEditing} className="press text-xs font-medium text-accent hover:underline">Edit limits</button>}>
        {!editing && report.limits_source === 'published' && report.published_note && <p className="selectable mb-3 rounded-control bg-fill px-3 py-2 text-xs leading-relaxed text-label-2">{report.published_note}</p>}
        {!editing && report.limits.length === 0 && <p className="text-xs leading-relaxed text-label-2">No limits are set, so Clank will not warn you. Choose Edit limits to set some.</p>}
        {!editing && (
          <ul className="flex flex-col gap-3.5">
            {report.limits.map((row) => (
              <li key={`${row.window}-${row.kind}`}>
                <p className="selectable text-[13px] text-label">{limitText(row)}</p>
                <Bar percent={row.percent} reached={row.reached} />
              </li>
            ))}
          </ul>
        )}
        {editing && form && (
          <div className="flex flex-col gap-3">
            <div className="grid grid-cols-[5rem_1fr_1fr] items-start gap-x-3 gap-y-2.5 text-xs">
              <span />
              <span className="font-medium text-label-2">Tokens</span>
              <span className="font-medium text-label-2">Requests</span>
              {WINDOW_ORDER.map((window) => (
                <div key={window} className="contents">
                  <span className="pt-1.5 text-label-2">{cap(window)}</span>
                  {(['tokens', 'requests'] as const).map((kind) => (
                    <div key={kind}>
                      <input value={form[window][kind]} onChange={(e) => change(window, kind, e.target.value)} disabled={busy} inputMode="numeric" spellCheck={false}
                        aria-label={`${cap(window)} ${kind} limit`} aria-invalid={errors[`${window}.${kind}`] ? true : undefined} placeholder="No limit" className={INPUT} />
                      {errors[`${window}.${kind}`] && <p className="selectable mt-1 text-danger">{errors[`${window}.${kind}`]}</p>}
                    </div>
                  ))}
                </div>
              ))}
            </div>
            <p className="text-xs leading-relaxed text-label-3">Minute and hour count the last 60 seconds and the last hour. Day, week (from Monday) and month follow your own calendar. Leave a field empty for no limit.</p>
            {failure && <p className="selectable text-xs text-danger">{describeError(failure).message}</p>}
            <div className="flex flex-wrap items-center gap-2">
              <button onClick={() => void run(() => onSaveLimits(payloadFromUsageForm(form)), () => setEditing(false))} disabled={busy || !dirty || Object.keys(errors).length > 0} className={BUTTON_PRIMARY}>
                {busy && <Loader2 className="h-3 w-3 animate-spin" />}Save limits
              </button>
              <button onClick={() => setEditing(false)} disabled={busy} className={BUTTON}>Cancel</button>
              <span className="flex-1" />
              {report.has_suggestion && report.limits_source !== 'published' && (
                <button onClick={() => void run(() => onSaveLimits(null), () => setEditing(false))} disabled={busy} className="press text-xs text-label-2 hover:text-label hover:underline">Use the suggested limits</button>
              )}
              {report.limits.length > 0 && <button onClick={() => void run(() => onSaveLimits({}), () => setEditing(false))} disabled={busy} className="press text-xs text-label-2 hover:text-danger hover:underline">Remove all limits</button>}
            </div>
          </div>
        )}
      </Section>

      <Section title={`Reported by ${cap(report.provider)}`}>
        {report.provider_reported.length === 0
          ? <p className="text-xs leading-relaxed text-label-2">The provider has not reported its limits yet. They appear here after the next answer.</p>
          : (
            <ul className="flex flex-col gap-3.5">
              {report.provider_reported.map((row) => (
                <li key={row.kind}>
                  <p className="selectable text-[13px] text-label">{providerRowText(row)}</p>
                  <Bar percent={row.percent} reached={row.used >= row.limit} />
                </li>
              ))}
            </ul>
          )}
      </Section>

      <Section title="Counted by Clank">
        <ul className="flex flex-col gap-1">
          {countsLines(report.counts).map((line) => <li key={line} className="selectable text-[13px] text-label">{line}</li>)}
        </ul>
        <p className="mt-2 text-xs leading-relaxed text-label-3">Clank only counts what it sent itself. It never stores your questions or answers here, only the numbers.</p>
        {report.counts.requests > 0 && (
          <div className="mt-3 flex items-center gap-2">
            {confirmReset ? (
              <>
                <span className="text-xs text-label-2">Reset all counts?</span>
                <button onClick={() => void run(onReset, () => setConfirmReset(false))} disabled={busy} className={BUTTON}>{busy && <Loader2 className="h-3 w-3 animate-spin" />}Reset</button>
                <button onClick={() => setConfirmReset(false)} disabled={busy} className="press text-xs text-label-2 hover:text-label">Cancel</button>
              </>
            ) : <button onClick={() => setConfirmReset(true)} className={BUTTON}>Reset counts</button>}
          </div>
        )}
        {!editing && failure && <p className="selectable mt-2 text-xs text-danger">{describeError(failure).message}</p>}
      </Section>

      <p className="text-xs leading-relaxed text-label-3">Limits only warn you. Nothing stops a question.</p>
    </div>
  )
}

export default UsageTab
