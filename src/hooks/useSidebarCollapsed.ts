import { useCallback, useEffect, useState } from 'react'
import { readFlag, writeFlag } from '../lib/flag'

const KEY = 'clank.sidebarCollapsed'

function storage() {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

/** Is the left sidebar folded away? Remembered, and toggled by the button or by Cmd/Ctrl + B. */
export function useSidebarCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => readFlag(storage(), KEY, false))
  const toggle = useCallback(() => {
    setCollapsed((current) => {
      writeFlag(storage(), KEY, !current)
      return !current
    })
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === 'b') {
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggle])
  return [collapsed, toggle]
}
