import { Cloud, Loader2, RefreshCw, Square } from 'lucide-react'
import type { IndexStatus, Project } from '../api/types'
import { progressFraction, projectStatus } from '../lib/present'

interface ProjectHeaderProps {
  project: Project | undefined
  index: IndexStatus | undefined
  allowRemote: boolean
  disabled: boolean
  onIndex: () => void
  onCancel: () => void
  onToggleRemote: (value: boolean) => void
}

function ProjectHeader({ project, index, allowRemote, disabled, onIndex, onCancel, onToggleRemote }: ProjectHeaderProps) {
  if (!project) {
    return (
      <header className="flex h-12 shrink-0 items-center border-b border-gray-200 px-4 dark:border-white/10">
        <p className="truncate text-sm font-semibold text-gray-900 dark:text-white">Select a project</p>
      </header>
    )
  }
  const status = projectStatus(project, index)
  const fraction = progressFraction(index)
  const running = status.tone === 'busy'

  return (
    <header className="shrink-0 border-b border-gray-200 dark:border-white/10">
      <div className="flex h-12 items-center gap-3 px-4">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-gray-900 dark:text-white">{project.name}</p>
          <p className="truncate text-xs text-gray-500 dark:text-gray-500" title={project.path}>{project.path}</p>
        </div>

        <label
          className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-gray-600 dark:text-gray-400"
          title="When on, the code excerpts found for a question are sent to the answer model's service. Embedding and search always stay on this computer."
        >
          <Cloud className="h-3.5 w-3.5" />
          <span>Send code to remote model</span>
          <input type="checkbox" checked={allowRemote} onChange={(e) => onToggleRemote(e.target.checked)} className="h-3.5 w-3.5 accent-gray-900 dark:accent-white" />
        </label>

        {running ? (
          <button onClick={onCancel} disabled={disabled || status.label === 'Stopping…'} className="flex shrink-0 items-center gap-1.5 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:opacity-50 dark:border-white/10 dark:text-gray-300 dark:hover:bg-gray-800">
            <Square className="h-3 w-3" />
            Cancel
          </button>
        ) : (
          <button onClick={onIndex} disabled={disabled} className="flex shrink-0 items-center gap-1.5 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-100 disabled:opacity-50 dark:border-white/10 dark:text-gray-300 dark:hover:bg-gray-800">
            <RefreshCw className="h-3 w-3" />
            {project.indexed ? 'Re-index' : 'Index'}
          </button>
        )}
      </div>

      <div className="flex items-center gap-2 px-4 pb-1.5 text-xs text-gray-500 dark:text-gray-500">
        {running && <Loader2 className="h-3 w-3 animate-spin" />}
        <span>{status.label}</span>
        {index?.current_file && running && <span className="truncate font-mono text-[11px] opacity-70">{index.current_file}</span>}
      </div>
      {fraction !== null && (
        <div className="h-0.5 w-full bg-gray-100 dark:bg-gray-900">
          <div className="h-full bg-gray-900 transition-all duration-300 dark:bg-white" style={{ width: `${Math.round(fraction * 100)}%` }} />
        </div>
      )}
      {index?.state === 'stopped' && index.message && <p className="px-4 pb-1.5 text-xs text-amber-600 dark:text-amber-400">{index.message}</p>}
      {index?.state === 'failed' && index.error && <p className="px-4 pb-1.5 text-xs text-red-600 dark:text-red-400">{index.error.message}</p>}
    </header>
  )
}

export default ProjectHeader
