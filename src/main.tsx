import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import { applyTheme, readTheme } from './hooks/useTheme'
import { resolveTheme } from './lib/theme'

// The window has vibrancy only on a Mac: styles that depend on it hang on this class.
if (/Mac/.test(navigator.userAgent)) document.documentElement.classList.add('mac')

// The saved theme (or the system's) goes on before the first frame; after that useTheme keeps it.
applyTheme(resolveTheme(readTheme(), window.matchMedia('(prefers-color-scheme: dark)').matches))

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
