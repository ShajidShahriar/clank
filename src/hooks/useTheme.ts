import { useCallback, useEffect, useState } from 'react'
import { getBackendControl } from '../bridge'
import { oppositeTheme, parseTheme, resolveTheme, type Theme } from '../lib/theme'

export const THEME_KEY = 'clank.theme'

function storage() {
  try {
    return window.localStorage
  } catch {
    return undefined
  }
}

export function readTheme(): Theme | null {
  try {
    return parseTheme(storage()?.getItem(THEME_KEY))
  } catch {
    return null
  }
}

/** Puts the theme on the page (the class `dark`) before anything is drawn, so there is no flash of the wrong one. */
export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
}

/** Light or dark: the person's saved choice, or the system's while they have made none. The toggle saves a choice. */
export function useTheme(): { theme: Theme, toggle: () => void } {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const [stored, setStored] = useState<Theme | null>(readTheme)
  const [systemDark, setSystemDark] = useState(media.matches)
  const theme = resolveTheme(stored, systemDark)

  useEffect(() => {
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [media])

  useEffect(() => {
    applyTheme(theme)
    getBackendControl()?.setTheme(stored ?? 'system')
  }, [theme, stored])

  const toggle = useCallback(() => {
    const next = oppositeTheme(theme)
    try {
      storage()?.setItem(THEME_KEY, next)
    } catch {
      /* blocked or full: the choice holds only until the window closes */
    }
    setStored(next)
  }, [theme])

  return { theme, toggle }
}
