import { CheckCircle2, KeyRound, Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import type { ApiError, LlmSettings, LlmTest, Result } from '../api/types'
import { describeError } from '../lib/present'
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

const INPUT = 'w-full rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-sm text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-300 disabled:opacity-60 dark:border-white/10 dark:bg-[#0a0a0a] dark:text-white dark:placeholder-gray-500 dark:focus:ring-gray-700'
const LABEL = 'mb-1 block text-xs font-medium text-gray-700 dark:text-gray-300'
const ERROR = 'mt-1 text-xs text-red-600 dark:text-red-400'

function Field({ label, error, hint, children }: { label: string, error?: string, hint?: string, children: React.ReactNode }) {
  return (
    <div>
      <label className={LABEL}>{label}</label>
      {children}
      {error && <p className={ERROR}>{error}</p>}
      {!error && hint && <p className="mt-1 text-xs text-gray-500 dark:text-gray-500">{hint}</p>}
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
    <div className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-xl border border-gray-200 bg-white shadow-xl dark:border-white/10 dark:bg-[#0a0a0a]">
      <div className="flex shrink-0 items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-white/10">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-white">Answer model</h2>
        <button onClick={onClose} className="rounded-md px-2 py-1 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800">Close</button>
      </div>

      <div className="flex flex-col gap-3.5 overflow-y-auto px-4 py-4">
        <Field label="Provider">
          <select value={form.preset} onChange={(e) => onPreset(e.target.value)} disabled={busy} className={INPUT}>
            {settings.presets.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <p className="mt-1 text-xs leading-relaxed text-gray-500 dark:text-gray-500">{preset.note}</p>
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
                <KeyRound className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-gray-400" />
                <input type="password" value={form.apiKey} onChange={(e) => onChange({ apiKey: e.target.value, removeKey: false })} disabled={busy} autoComplete="off" spellCheck={false}
                  placeholder={keyIsSaved ? '••••••••••••' : 'gsk_…'} className={`${INPUT} pl-8`} />
              </div>
              {keyIsSaved && (
                <button type="button" onClick={() => onChange({ removeKey: !form.removeKey, apiKey: '' })} disabled={busy}
                  className={`shrink-0 rounded-md border px-2.5 py-1.5 text-xs font-medium ${form.removeKey ? 'border-red-300 text-red-600 dark:border-red-500/40 dark:text-red-400' : 'border-gray-200 text-gray-700 hover:bg-gray-100 dark:border-white/10 dark:text-gray-300 dark:hover:bg-gray-800'}`}>
                  {form.removeKey ? 'Will be removed' : 'Remove key'}
                </button>
              )}
            </div>
          </Field>
        )}

        <details className="text-xs text-gray-600 dark:text-gray-400">
          <summary className="cursor-pointer select-none font-medium text-gray-700 dark:text-gray-300">Limits</summary>
          <div className="mt-2 grid grid-cols-2 gap-3">
            <Field label="Code sent per question" error={errors.context_tokens} hint="Estimated tokens, 500 to 16000">
              <input value={form.context_tokens} onChange={(e) => onChange({ context_tokens: e.target.value })} disabled={busy} inputMode="numeric" className={INPUT} />
            </Field>
            <Field label="Longest answer" error={errors.max_output_tokens} hint="Tokens, 1 to 20000">
              <input value={form.max_output_tokens} onChange={(e) => onChange({ max_output_tokens: e.target.value })} disabled={busy} inputMode="numeric" className={INPUT} />
            </Field>
          </div>
        </details>

        <p className="flex items-start gap-1.5 rounded-md bg-gray-50 px-2.5 py-2 text-xs leading-relaxed text-gray-600 dark:bg-white/5 dark:text-gray-400">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{privacyNote(form, preset)}</span>
        </p>

        {saveError && (
          <p className="flex items-start gap-1.5 text-xs text-red-600 dark:text-red-400">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{describeError(saveError).message}</span>
          </p>
        )}
        {saved && !dirty && !saveError && (
          <p className="flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400"><CheckCircle2 className="h-3.5 w-3.5 shrink-0" />Saved.</p>
        )}
        {testResult && testResult.ok && (
          <p className="flex items-center gap-1.5 text-xs text-green-700 dark:text-green-400">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
            <span>Connected: {testResult.data.model} answered in {testResult.data.latency_ms} ms.</span>
          </p>
        )}
        {testResult && !testResult.ok && (
          <p className="flex items-start gap-1.5 text-xs text-red-600 dark:text-red-400">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{describeError(testResult.error).message}</span>
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-end gap-2 border-t border-gray-200 px-4 py-3 dark:border-white/10">
        <button onClick={onTest} disabled={busy || hasProblems}
          className="flex items-center gap-1.5 rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:text-gray-300 dark:hover:bg-gray-800">
          {testing && <Loader2 className="h-3 w-3 animate-spin" />}
          {dirty ? 'Save and test' : 'Test connection'}
        </button>
        <button onClick={onSave} disabled={busy || hasProblems || !dirty}
          className="flex items-center gap-1.5 rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-white dark:text-black dark:hover:bg-gray-100">
          {saving && <Loader2 className="h-3 w-3 animate-spin" />}
          Save
        </button>
      </div>
    </div>
  )
}

export default SettingsForm
