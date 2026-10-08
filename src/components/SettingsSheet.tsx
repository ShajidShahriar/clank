import type { ReactNode } from 'react'

export type SettingsTab = 'model' | 'usage'

const TABS: Array<{ id: SettingsTab, label: string }> = [{ id: 'model', label: 'Answer model' }, { id: 'usage', label: 'Usage' }]

/** The frame of the settings: two tabs and Done. What is inside belongs to the tab. */
function SettingsSheet({ tab, onTab, onClose, children }: { tab: SettingsTab, onTab: (tab: SettingsTab) => void, onClose: () => void, children: ReactNode }) {
  return (
    <div className="animate-sheet flex h-[640px] max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-sheet bg-bg shadow-float">
      <div className="flex shrink-0 items-center justify-between px-5 pb-1 pt-4">
        <div role="tablist" aria-label="Settings" className="flex rounded-full bg-fill p-[3px] text-[13px] font-medium">
          {TABS.map(({ id, label }) => (
            <button key={id} role="tab" aria-selected={tab === id} onClick={() => onTab(id)}
              className={`rounded-full px-3.5 py-1 transition-colors duration-200 ${tab === id ? 'bg-bg text-label shadow-[0_1px_3px_rgb(0_0_0/0.12)]' : 'text-label-2 hover:text-label'}`}>
              {label}
            </button>
          ))}
        </div>
        <button onClick={onClose} className="press rounded-control px-2 py-1 text-[13px] font-medium text-accent hover:bg-accent-soft">Done</button>
      </div>
      {children}
    </div>
  )
}

export default SettingsSheet
