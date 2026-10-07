import { useEffect, useState } from 'react'

/** The current time in milliseconds, refreshed every `intervalMs` while `active` (so a live timer can tick); a still value when nothing is live. */
export function useNow(active: boolean, intervalMs = 100): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [active, intervalMs])
  return now
}
