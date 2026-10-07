// What the window may ask the main process to do with the app's theme: light, dark or follow the system. Anything else means the system.
// (The window needs the main process for this because the Mac's see-through sidebar takes its look from the app's theme, not from the page's colors.)
export type ThemeSource = 'light' | 'dark' | 'system'

export function themeSource(value: unknown): ThemeSource {
  return value === 'light' || value === 'dark' ? value : 'system'
}
