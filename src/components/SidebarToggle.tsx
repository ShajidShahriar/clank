import { PanelLeft } from 'lucide-react'
import { ICON_BUTTON } from './ui'

/**
 * The button that folds the sidebar away. It floats at the top left, next to the Mac's traffic lights, and slides to the corner when they are hidden in full
 * screen. The sliding is done by the box around the button: the button's own `press` class sets its own transition, which would replace this one.
 */
function SidebarToggle({ collapsed, onToggle }: { collapsed: boolean, onToggle: () => void }) {
  return (
    <div className="no-drag absolute left-4 top-[11px] z-30 transition-[left] duration-[400ms] ease-[cubic-bezier(0.2,0.9,0.3,1)] motion-reduce:duration-100 in-[.mac:not(.fs)]:left-[82px]">
      <button
        onClick={onToggle}
        aria-label={collapsed ? 'Show sidebar' : 'Hide sidebar'}
        aria-expanded={!collapsed}
        title={`${collapsed ? 'Show' : 'Hide'} sidebar (⌘B)`}
        className={`${ICON_BUTTON} no-drag`}
      >
        <PanelLeft className="h-[18px] w-[18px]" strokeWidth={1.6} />
      </button>
    </div>
  )
}

export default SidebarToggle
