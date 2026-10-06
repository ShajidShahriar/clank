import { useCallback, useEffect, useMemo, useState } from 'react'
import type { createApi } from '../api/client'
import type { ApiError, LlmSettings, LlmTest, Result } from '../api/types'
import { formFromSettings, isDirty, payloadFromForm, switchPreset, validateForm, type Form } from '../lib/settingsForm'
import { describeError } from '../lib/present'
import SettingsForm from './SettingsForm'

type Api = ReturnType<typeof createApi>

interface SettingsDialogProps {
  api: Api
  onClose: () => void
  onSaved: () => void
}

/** The answer-model settings dialog: loads the current settings, lets the person change them, saves them (the key goes through the desktop app's own door) and tests the connection. */
function SettingsDialog({ api, onClose, onSaved }: SettingsDialogProps) {
  const [settings, setSettings] = useState<LlmSettings | null>(null)
  const [loadError, setLoadError] = useState<ApiError | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState<ApiError | null>(null)
  const [testResult, setTestResult] = useState<Result<LlmTest> | null>(null)

  useEffect(() => {
    let alive = true
    api.getLlmSettings().then((result) => {
      if (!alive) return
      if (result.ok) {
        setSettings(result.data)
        setForm(formFromSettings(result.data))
      } else {
        setLoadError(result.error)
      }
    })
    return () => { alive = false }
  }, [api])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const preset = settings && form ? (settings.presets.find((p) => p.id === form.preset) ?? settings.presets[0]) : undefined
  const errors = useMemo(() => (form && preset ? validateForm(form, preset) : {}), [form, preset])
  const dirty = settings && form ? isDirty(form, settings) : false

  const change = useCallback((patch: Partial<Form>) => {
    setForm((current) => (current ? { ...current, ...patch } : current))
    setSaved(false)
    setTestResult(null)
  }, [])

  const choosePreset = useCallback((id: string) => {
    if (!settings) return
    setForm((current) => (current ? switchPreset(current, settings, id) : current))
    setSaved(false)
    setSaveError(null)
    setTestResult(null)
  }, [settings])

  /** Saves, then reads the new settings back. Returns true when it worked. */
  const save = useCallback(async (): Promise<boolean> => {
    if (!form || !preset) return false
    setSaving(true)
    setSaveError(null)
    const payload = payloadFromForm(form, preset)
    const result = await api.saveLlmSettings(payload.config, payload.apiKey)
    setSaving(false)
    if (!result.ok) {
      setSaveError(result.error)
      return false
    }
    setSettings(result.data)
    setForm(formFromSettings(result.data))                       // the typed key is gone from the form the moment it is saved
    setSaved(true)
    onSaved()
    return true
  }, [api, form, preset, onSaved])

  const test = useCallback(async () => {
    if (dirty && !(await save())) return
    setTesting(true)
    setTestResult(null)
    const result = await api.testLlm()
    setTesting(false)
    setTestResult(result)
  }, [api, dirty, save])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }} role="dialog" aria-modal="true" aria-label="Answer model settings">
      {settings && form ? (
        <SettingsForm settings={settings} form={form} errors={errors} dirty={dirty} saving={saving} testing={testing} saved={saved} saveError={saveError} testResult={testResult}
          onChange={change} onPreset={choosePreset} onSave={() => void save()} onTest={() => void test()} onClose={onClose} />
      ) : (
        <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-700 shadow-xl dark:border-white/10 dark:bg-[#0a0a0a] dark:text-gray-300">
          {loadError ? (
            <>
              <p>{describeError(loadError).message}</p>
              <button onClick={onClose} className="mt-3 rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium dark:border-white/10">Close</button>
            </>
          ) : (
            <p>Loading the settings…</p>
          )}
        </div>
      )}
    </div>
  )
}

export default SettingsDialog
