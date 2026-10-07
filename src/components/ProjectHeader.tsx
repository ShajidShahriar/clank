import { Cloud, Loader2, RefreshCw, Square } from 'lucide-react'
import type { IndexStatus, Project } from '../api/types'
import { progressFraction, projectStatus } from '../lib/present'
import { BUTTON } from './ui'

interface ProjectHeaderProps {
  sidebarCollapsed: boolean
  project: Project | undefined
  index: IndexStatus | undefined
  allowRemote: boolean
  disabled: boolean
  onIndex: () => void
  onCancel: () => void
  onToggleRemote: (value: boolean) => void
}

/** Chat and Code, side by side. Code is not built yet: it is shown so people can see where it will live. */
function ModeToggle() {
  return (
    <div role="tablist" aria-label="Mode" className="no-drag flex shrink-0 rounded-full bg-fill p-[3px] text-[13px] font-medium">
      <button role="tab" aria-selected className="rounded-full bg-bg px-5 py-1 text-label shadow-[0_1px_3px_rgb(0_0_0/0.12)]">Chat</button>
      <button role="tab" aria-selected={false} disabled title="Code is coming soon" className="cursor-not-allowed rounded-full px-5 py-1 text-label-3">Code</button>
    </div>
  )
}

function ProjectHeader({ sidebarCollapsed, project, index, allowRemote, disabled, onIndex, onCancel, onToggleRemote }: ProjectHeaderProps) {
  const status = project ? projectStatus(project, index) : null
  const fraction = progressFraction(index)
  const running = status?.tone === 'busy'
  const showStatus = project && (running || !project.indexed || index?.state === 'stopped' || index?.state === 'failed')
  const inset = sidebarCollapsed ? 'pl-14 in-[.mac:not(.fs)]:pl-[122px]' : 'pl-5'

  return (
    <header className="shrink-0">
      <div className={`drag grid h-[52px] grid-cols-[1fr_auto_1fr] items-center gap-3 pr-5 transition-[padding] duration-[400ms] ease-[cubic-bezier(0.2,0.9,0.3,1)] motion-reduce:duration-100 ${inset}`}>
        <div className="min-w-0">
          {project && (
            <>
              <p className="truncate text-sm font-semibold leading-tight text-label">{project.name}</p>
              <p className="truncate text-xs text-label-3" title={project.path}>{project.path}</p>
            </>
          )}
        </div>

        <ModeToggle />

        <div className="flex min-w-0 items-center justify-end gap-3">
          {project && (
            <>
              <label
                className="no-drag flex shrink-0 cursor-pointer items-center gap-2 text-xs text-label-2"
                title="When on, the code excerpts found for a question are sent to the answer model's service. Embedding and search always stay on this computer."
              >
                <Cloud className="h-3.5 w-3.5" strokeWidth={1.75} />
                <span className="hidden min-[1180px]:inline">Send code to remote model</span>
                <input type="checkbox" role="switch" aria-label="Send code to remote model" checked={allowRemote} onChange={(e) => onToggleRemote(e.target.checked)} className="peer sr-only" />
                <span aria-hidden className="relative h-[18px] w-[30px] rounded-full bg-fill-strong transition-colors duration-200 peer-checked:bg-accent peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent after:absolute after:left-[2px] after:top-[2px] after:h-[14px] after:w-[14px] after:rounded-full after:bg-white after:shadow-[0_1px_2px_rgb(0_0_0/0.3)] after:transition-transform after:duration-200 after:ease-[cubic-bezier(0.2,1,0.3,1)] peer-checked:after:translate-x-[12px]" />
              </label>

              {running ? (
                <button onClick={onCancel} disabled={disabled || status?.label === 'Stopping…'} className={`${BUTTON} no-drag`}>
                  <Square className="h-3 w-3" strokeWidth={1.75} />
                  Cancel
                </button>
              ) : (
                <button onClick={onIndex} disabled={disabled} className={`${BUTTON} no-drag`}>
                  <RefreshCw className="h-3 w-3" strokeWidth={1.75} />
                  {project.indexed ? 'Re-index' : 'Index'}
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {showStatus && status && (
        <div className={`flex items-center gap-2 pb-2 pr-5 text-xs text-label-3 ${inset}`}>
          {running && <Loader2 className="h-3 w-3 animate-spin" />}
          <span className={running ? 'text-accent' : ''}>{status.label}</span>
          {index?.current_file && running && <span className="truncate font-mono text-[11px] opacity-80">{index.current_file}</span>}
        </div>
      )}
      {fraction !== null && (
        <div className="mx-5 h-[3px] overflow-hidden rounded-full bg-fill">
          <div className="h-full rounded-full bg-accent transition-[width] duration-300 ease-out" style={{ width: `${Math.round(fraction * 100)}%` }} />
        </div>
      )}
      {index?.state === 'stopped' && index.message && <p className={`selectable pb-2 pr-5 text-xs text-warn ${inset}`}>{index.message}</p>}
      {index?.state === 'failed' && index.error && <p className={`selectable pb-2 pr-5 text-xs text-danger ${inset}`}>{index.error.message}</p>}
    </header>
  )
}

export default ProjectHeader
