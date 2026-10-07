import { CheckCircle2, KeyRound, Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import type { ApiError, LlmSettings, LlmTest, Result } from '../api/types'
import { describeError } from '../lib/present'
import { BUTTON, BUTTON_PRIMARY, INPUT } from './ui'
import { privacyNote, type Form, type FormErrors } from '../lib/settingsForm'

interface SettingsFormProps {
  settings: LlmSettings
  form: Form
  errors: FormErrors
  dirty: boolean
  saving: boolean
  testing: boolean
  saved: boolean
  saveError: ApiError | null
  testResult: Result<LlmTest> | null
  onChange: (change: Partial<Form>) => void
  onPreset: (id: string) => void
  onSave: () => void
  onTest: () => void
  onClose: () => void
}

const LABEL = 'mb-1 block text-xs font-medium text-label-2'
const ERROR = 'selectable mt-1 text-xs text-danger'

function Field({ label, error, hint, children }: { label: string, error?: string, hint?: string, children: React.ReactNode }) {
  return (
    <div>
      <label className={LABEL}>{label}</label>
      {children}
      {error && <p className={ERROR}>{error}</p>}
      {!error && hint && <p className="mt-1 text-xs text-label-3">{hint}</p>}
    </div>
  )
}

/** The answer-model settings, drawn. All the logic is in lib/settingsForm.ts; the stateful wrapper is SettingsDialog. */
function SettingsForm({ settings, form, errors, dirty, saving, testing, saved, saveError, testResult, onChange, onPreset, onSave, onTest, onClose }: SettingsFormProps) {
  const preset = settings.presets.find((p) => p.id === form.preset) ?? settings.presets[0]
  const active = settings.active
  const hasProblems = Object.keys(errors).length > 0
  const keyIsSaved = active.preset === form.preset && active.key_set
  const busy = saving || testing

  return (
    <div className="animate-sheet flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-sheet bg-bg shadow-float">
      <div className="flex shrink-0 items-center justify-between px-5 pb-1 pt-4">
        <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-label">Answer model</h2>
        <button onClick={onClose} className="press rounded-control px-2 py-1 text-[13px] font-medium text-accent hover:bg-accent-soft">Done</button>
      </div>

      <div className="flex flex-col gap-4 overflow-y-auto px-5 py-5">
        <Field label="Provider">
          <select value={form.preset} onChange={(e) => onPreset(e.target.value)} disabled={busy} className={INPUT}>
            {settings.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <p className="mt-1 text-xs leading-relaxed text-label-3">{preset.note}</p>
        </Field>

        {preset.base_url_editable && (
          <Field label="Address" error={errors.base_url} hint="The OpenAI-compatible address, for example http://localhost:11434/v1">
            <input value={form.base_url} onChange={(e) => onChange({ base_url: e.target.value })} disabled={busy} spellCheck={false} placeholder="https://your-server/v1" className={INPUT} />
          </Field>
        )}

        <Field label="Model" error={errors.model} hint="The exact model name the service knows, for example openai/gpt-oss-120b or llama3.2">
          <input value={form.model} onChange={(e) => onChange({ model: e.target.value })} disabled={busy} spellCheck={false} className={INPUT} />
        </Field>

        {preset.takes_key && (
          <Field label={preset.key_optional ? 'API key (optional)' : 'API key'} error={errors.apiKey}
            hint={keyIsSaved ? 'A key is saved. Leave this empty to keep it.' : preset.key_optional ? 'Leave empty if the service needs no key.' : 'Paste your key. It is stored encrypted on this computer.'}>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <KeyRound className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-label-3" />
                <input type="password" value={form.apiKey} onChange={(e) => onChange({ apiKey: e.target.value, removeKey: false })} disabled={busy} autoComplete="off" spellCheck={false}
                  placeholder={keyIsSaved ? '••••••••••••' : 'gsk_…'} className={`${INPUT} pl-8`} />
              </div>
              {keyIsSaved && (
                <button type="button" onClick={() => onChange({ removeKey: !form.removeKey, apiKey: '' })} disabled={busy}
                  className={`press shrink-0 rounded-control px-2.5 py-1.5 text-xs font-medium ${form.removeKey ? 'bg-danger-soft text-danger' : 'bg-fill text-label hover:bg-fill-strong'}`}>
                  {form.removeKey ? 'Will be removed' : 'Remove key'}
                </button>
              )}
            </div>
          </Field>
        )}

        <details className="text-xs text-label-2">
          <summary className="cursor-pointer select-none font-medium text-label-2 hover:text-label">Limits</summary>
          <div className="mt-2 grid grid-cols-2 gap-3">
            <Field label="Code sent per question" error={errors.context_tokens} hint="Estimated tokens, 500 to 16000">
              <input value={form.context_tokens} onChange={(e) => onChange({ context_tokens: e.target.value })} disabled={busy} inputMode="numeric" className={INPUT} />
            </Field>
            <Field label="Longest answer" error={errors.max_output_tokens} hint="Tokens, 1 to 20000">
              <input value={form.max_output_tokens} onChange={(e) => onChange({ max_output_tokens: e.target.value })} disabled={busy} inputMode="numeric" className={INPUT} />
            </Field>
          </div>
        </details>

        <p className="selectable flex items-start gap-2 rounded-card bg-card px-3 py-2.5 text-xs leading-relaxed text-label-2">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{privacyNote(form, preset)}</span>
        </p>

        {saveError && (
          <p className="flex items-start gap-1.5 text-xs text-danger">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{describeError(saveError).message}</span>
          </p>
        )}
        {saved && !dirty && !saveError && (
          <p className="flex items-center gap-1.5 text-xs text-ok"><CheckCircle2 className="h-3.5 w-3.5 shrink-0" />Saved.</p>
        )}
        {testResult && testResult.ok && (
          <p className="flex items-center gap-1.5 text-xs text-ok">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
            <span>Connected: {testResult.data.model} answered in {testResult.data.latency_ms} ms.</span>
          </p>
        )}
        {testResult && !testResult.ok && (
          <p className="flex items-start gap-1.5 text-xs text-danger">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{describeError(testResult.error).message}</span>
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-end gap-2 px-5 pb-4 pt-2">
        <button onClick={onTest} disabled={busy || hasProblems} className={BUTTON}>
          {testing && <Loader2 className="h-3 w-3 animate-spin" />}
          {dirty ? 'Save and test' : 'Test connection'}
        </button>
        <button onClick={onSave} disabled={busy || hasProblems || !dirty} className={BUTTON_PRIMARY}>
          {saving && <Loader2 className="h-3 w-3 animate-spin" />}
          Save
        </button>
      </div>
    </div>
  )
}

export default SettingsForm
